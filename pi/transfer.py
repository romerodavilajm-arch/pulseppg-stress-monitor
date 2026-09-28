"""Transferencia del JSONL al watcher (docs/Propuesta técnica.md, fase 4).

No depende del sensor: lo usan igual la Pi real y el simulador.

Estructura en disco (BASE_DIR, por defecto /tmp/ppg):

  <session_id>.jsonl            captura en curso o recién terminada
  completed/<session_id>.jsonl  el watcher la confirmó (200 o 409)
  pending/<session_id>.jsonl    falló tras 3 intentos; se reintenta más tarde
  pending/<session_id>.json     metadata para ese reintento
  pending/<session_id>.abort.json  aviso de cancelación que el watcher no
                                   recibió (Fase 9); se reintenta igual
"""

import hashlib
import json
import logging
import os
import shutil
import time

import requests

# Espera antes de cada intento: 0 s, 2 s, 5 s (sección 8).
BACKOFF_SEC = (0, 2, 5)
TIMEOUT_SEC = 30
# El aviso de abort se manda antes de volver a bienvenida: un solo intento
# corto para no dejar al usuario esperando si el watcher no responde.
ABORT_TIMEOUT_SEC = 3

log = logging.getLogger("transfer")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _post(watcher_url, path, meta, checksum):
    """Un intento. Devuelve True si el watcher ya tiene la sesión (200 o 409)."""
    with open(path, "rb") as f:
        resp = requests.post(
            f"{watcher_url}/upload",
            data={**meta, "checksum": checksum},
            files={"file": (os.path.basename(path), f, "application/x-ndjson")},
            timeout=TIMEOUT_SEC,
        )
    if resp.status_code == 200:
        return True
    if resp.status_code == 409:
        # La sesión ya está en un estado posterior: reintentar no cambia nada.
        log.warning("%s: el watcher respondió 409 (%s)", meta["session_id"], resp.text.strip())
        return True
    # 400 (checksum, posible corrupción en el camino) y 5xx: se reintenta.
    log.warning("%s: el watcher respondió %d (%s)", meta["session_id"], resp.status_code, resp.text.strip())
    return False


def upload(watcher_url, path, meta, base_dir):
    """Sube path con 3 intentos y lo mueve a completed/ o pending/.

    meta: session_id, duration, quality, device_id, start_time.
    Devuelve True si el watcher confirmó la recepción.
    """
    session_id = meta["session_id"]
    checksum = sha256_file(path)
    for attempt, wait in enumerate(BACKOFF_SEC, start=1):
        time.sleep(wait)
        try:
            if _post(watcher_url, path, meta, checksum):
                log.info("%s: subido en el intento %d (sha256 %s…)", session_id, attempt, checksum[:12])
                _move(path, os.path.join(base_dir, "completed"))
                return True
        except requests.RequestException as exc:
            log.warning("%s: intento %d sin respuesta (%s)", session_id, attempt, exc)

    log.error("%s: no se pudo subir tras %d intentos, queda en pending/", session_id, len(BACKOFF_SEC))
    dest = _move(path, os.path.join(base_dir, "pending"))
    with open(os.path.splitext(dest)[0] + ".json", "w") as f:
        json.dump(meta, f)
    return False


def _post_abort(watcher_url, meta):
    """Un intento. True si ya no hay nada que reintentar (200, 400 o 409)."""
    resp = requests.post(f"{watcher_url}/abort", data=meta, timeout=ABORT_TIMEOUT_SEC)
    if resp.status_code == 200:
        return True
    log.warning("%s: el watcher respondió %d al abort (%s)", meta["session_id"], resp.status_code, resp.text.strip())
    # 400 y 409 no cambian al reintentar; solo 5xx vale la pena guardarlo.
    return resp.status_code < 500


def report_abort(watcher_url, meta, base_dir):
    """Fase 9: avisa al watcher que la sesión se canceló (queda en 'error').

    meta: session_id, duration (segundos capturados), device_id, start_time.
    Si el watcher no responde, el aviso queda en pending/ y se reintenta con
    retry_pending. Devuelve True si el watcher lo registró.
    """
    try:
        if _post_abort(watcher_url, meta):
            log.info("%s: abort registrado en el watcher", meta["session_id"])
            return True
    except requests.RequestException as exc:
        log.warning("%s: abort sin respuesta del watcher (%s)", meta["session_id"], exc)
    pending = os.path.join(base_dir, "pending")
    os.makedirs(pending, exist_ok=True)
    with open(os.path.join(pending, f"{meta['session_id']}.abort.json"), "w") as f:
        json.dump(meta, f)
    log.error("%s: el abort queda en pending/", meta["session_id"])
    return False


def retry_pending(watcher_url, base_dir):
    """Reintenta lo que quedó en pending/ (al arrancar y antes de cada sesión)."""
    pending = os.path.join(base_dir, "pending")
    if not os.path.isdir(pending):
        return
    for name in sorted(os.listdir(pending)):
        if name.endswith(".abort.json"):
            _retry_abort(watcher_url, os.path.join(pending, name))
            continue
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(pending, name)
        meta_path = os.path.splitext(path)[0] + ".json"
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            log.error("%s: sin metadata válida, se deja en pending/", name)
            continue
        log.info("%s: reintentando desde pending/", meta["session_id"])
        if upload(watcher_url, path, meta, base_dir):
            os.remove(meta_path)


def _retry_abort(watcher_url, path):
    try:
        with open(path) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        log.error("%s: aviso de abort ilegible, se deja en pending/", os.path.basename(path))
        return
    log.info("%s: reintentando el abort desde pending/", meta["session_id"])
    try:
        if _post_abort(watcher_url, meta):
            os.remove(path)
            log.info("%s: abort registrado en el watcher", meta["session_id"])
    except requests.RequestException as exc:
        log.warning("%s: abort sin respuesta del watcher (%s)", meta["session_id"], exc)


def clear_completed(base_dir):
    """Al empezar una sesión nueva el respaldo de la anterior ya no hace falta."""
    shutil.rmtree(os.path.join(base_dir, "completed"), ignore_errors=True)


def _move(path, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        os.replace(path, dest)
    return dest

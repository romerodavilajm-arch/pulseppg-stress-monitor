"""watcher — Fases 4 y 5: recepción del archivo crudo y lanzamiento del análisis.

POST /upload (docs/Propuesta técnica.md, sección 8), multipart/form-data:

  session_id  <timestamp>_<device_id>, clave de idempotencia
  checksum    SHA-256 del archivo, en hexadecimal
  duration    segundos capturados
  quality     calidad que reporta la Pi
  device_id   (opcional) identificador de la Pi
  start_time  (opcional) inicio de la captura en ISO 8601; si falta se usa
              ahora - duration
  file        el JSONL, una línea {"t", "ir", "red"} por muestra

Respuestas:

  200  archivo guardado en /data/raw/<session_id>.jsonl y sesión en 'uploaded'
       (también si es un reintento del mismo archivo ya recibido)
  400  checksum no coincide, campos inválidos o JSONL mal formado
  409  la sesión ya pasó de 'uploaded' (o ya se recibió con otro checksum)
  500  error del watcher

El archivo se escribe primero en /data/raw/.incoming/ y solo se mueve a
/data/raw/ cuando el checksum coincide y la base lo acepta, así quien vigile
/data/raw/ (el watchdog de la Fase 5) nunca ve un archivo a medias.

Fase 5: watch.py vigila /data/raw/ y runner.py lanza Spark por cada archivo
nuevo ('uploaded' -> 'processing' -> 'ready' o 'error'). Al arrancar se
retoman las sesiones que quedaron en 'uploaded' o 'processing'.
"""

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from flask import Flask, jsonify, request

import runner
import watch

RAW_DIR = os.environ.get("RAW_DIR", "/data/raw")
INCOMING_DIR = os.path.join(RAW_DIR, ".incoming")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://pulseppg:pulseppg@postgres:5432/pulseppg"
)
# 5 min a 50 Hz son ~600 KB; el límite solo evita llenar el disco por error.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))

# El session_id acaba siendo un nombre de archivo: nada de "/" ni "..".
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.json.ensure_ascii = False
log = logging.getLogger("watcher")


def fail(code, message, session_id=None):
    log.warning("upload %s rechazado (%d): %s", session_id or "?", code, message)
    return jsonify({"ok": False, "error": message}), code


def validate_jsonl(path):
    """Devuelve (número de muestras, error). Cada línea debe tener t, ir y red."""
    count = 0
    with open(path, "rb") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                float(row["t"]), float(row["ir"]), float(row["red"])
            except (ValueError, KeyError, TypeError):
                return count, f"línea {n} no es una muestra válida"
            count += 1
    if count == 0:
        return 0, "el archivo no tiene muestras"
    return count, None


@app.get("/health")
def health():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        return {"ok": False, "db": str(exc)}, 503
    return {"ok": True}


@app.post("/upload")
def upload():
    form = request.form
    session_id = form.get("session_id", "")
    checksum = form.get("checksum", "").lower()
    upfile = request.files.get("file")

    if not SESSION_ID_RE.match(session_id):
        return fail(400, "session_id inválido", session_id)
    if not CHECKSUM_RE.match(checksum):
        return fail(400, "checksum debe ser SHA-256 en hexadecimal", session_id)
    if upfile is None:
        return fail(400, "falta el campo file", session_id)
    try:
        duration = float(form["duration"])
        if duration < 0:
            raise ValueError
    except (KeyError, ValueError):
        return fail(400, "duration debe ser un número >= 0", session_id)
    try:
        start_time = datetime.fromisoformat(form["start_time"]) if form.get("start_time") else None
    except ValueError:
        return fail(400, "start_time debe estar en ISO 8601", session_id)
    if start_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(seconds=duration)
    elif start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    quality = form.get("quality") or None
    device_id = form.get("device_id") or None

    # 1. Guardar en .incoming/ calculando el SHA-256 mientras se escribe.
    os.makedirs(INCOMING_DIR, exist_ok=True)
    # Nombre único: dos reintentos simultáneos no se pisan el temporal.
    tmp_path = os.path.join(INCOMING_DIR, f"{session_id}.{uuid.uuid4().hex}.part")
    final_path = os.path.join(RAW_DIR, f"{session_id}.jsonl")
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            for chunk in iter(lambda: upfile.stream.read(64 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)

        # 2. Checksum y formato.
        if digest.hexdigest() != checksum:
            return fail(400, "checksum no coincide", session_id)
        samples, error = validate_jsonl(tmp_path)
        if error:
            return fail(400, error, session_id)

        # 3. Máquina de estados + idempotencia. El archivo se mueve a /data/raw/
        #    dentro de la transacción: si la base rechaza, no queda nada visible.
        with psycopg.connect(DATABASE_URL) as conn, conn.transaction():
            row = conn.execute(
                """
                INSERT INTO sessions (session_id, device_id, start_time, duration_sec,
                                      quality, checksum, status, status_detail)
                VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', 'upload')
                ON CONFLICT (session_id) DO NOTHING
                RETURNING id
                """,
                (session_id, device_id, start_time, duration, quality, checksum),
            ).fetchone()

            if row is not None:
                pk, outcome = row[0], "nueva"
            else:
                pk, status, stored = conn.execute(
                    "SELECT id, status, checksum FROM sessions WHERE session_id = %s FOR UPDATE",
                    (session_id,),
                ).fetchone()
                if status == "created":
                    # servidor-web la registró al recibir start (fase posterior).
                    conn.execute(
                        """
                        UPDATE sessions
                        SET status = 'uploaded', status_detail = 'upload', checksum = %s,
                            duration_sec = %s, quality = %s,
                            device_id = COALESCE(device_id, %s)
                        WHERE id = %s
                        """,
                        (checksum, duration, quality, device_id, pk),
                    )
                    outcome = "created -> uploaded"
                elif status == "uploaded" and stored == checksum:
                    # Reintento de la Pi porque se perdió el 200: mismo efecto.
                    outcome = "reintento"
                elif status == "uploaded":
                    return fail(409, "la sesión ya se recibió con otro checksum", session_id)
                else:
                    return fail(409, f"la sesión ya está en '{status}'", session_id)

            os.replace(tmp_path, final_path)

        log.info(
            "upload %s OK (%s): pk=%d, %d muestras, %d bytes, %.1f s, calidad=%s",
            session_id, outcome, pk, samples, size, duration, quality,
        )
        return jsonify({"ok": True, "session_pk": pk, "samples": samples, "outcome": outcome})
    except psycopg.Error:
        log.exception("upload %s: error de base de datos", session_id)
        return jsonify({"ok": False, "error": "error de base de datos"}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    os.makedirs(INCOMING_DIR, exist_ok=True)

    # Fase 5: primero el watchdog y luego la recuperación, así ningún archivo
    # queda sin atender entre las dos. Si una sesión se encola dos veces, el
    # runner la procesa una sola (la segunda ya no está en 'uploaded').
    analysis = runner.Runner()
    analysis.start()
    watch.watch(RAW_DIR, analysis.submit)
    analysis.recover()

    port = int(os.environ.get("PORT", "5001"))
    # Werkzeug con hilos basta para una Pi en la red local.
    app.run(host="0.0.0.0", port=port, threaded=True)

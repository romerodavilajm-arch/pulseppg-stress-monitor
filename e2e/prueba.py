"""Prueba de punta a punta — Fase 11.

Hace de navegador frente al sistema levantado con un solo `docker compose up`
y recorre el flujo completo sin intervención manual:

  1. Servicios: servidor-web y watcher sanos, la Pi (o el simulador)
     registrada y la página responde.
  2. Sesión completa: `start` → captura → `uploading` → `done` → el watcher
     lanza spark y modelo → la sesión queda en 'ready' con métricas, ventanas
     de estrés e historial uploaded → processing → ready, y el crudo se borró
     de /data/raw/.
  3. Cancelación: `start` y `abort` a los pocos segundos → la sesión queda en
     'error' con status_detail "abort: ..." (historial created → error).

Solo usa lo que usa el navegador (Socket.IO y la API HTTP) más el volumen
rawdata en solo lectura. Se corre con

  docker compose --profile e2e run --rm e2e

Termina con código 0 si todo pasó y 1 si algo falló.

Variables de entorno:
  WEB_URL               servidor-web (por defecto http://servidor-web:5000)
  WATCHER_URL           watcher (por defecto http://watcher:5001)
  RAW_DIR               volumen rawdata montado (por defecto /data/raw; si no
                        existe se salta esa comprobación)
  ANALYSIS_TIMEOUT_SEC  espera máxima de 'done' a 'ready' (por defecto 600)
  SKIP_ABORT            1 = no probar la cancelación
"""

import os
import sys
import threading
import time

import requests
import socketio

WEB_URL = os.environ.get("WEB_URL", "http://servidor-web:5000").rstrip("/")
WATCHER_URL = os.environ.get("WATCHER_URL", "http://watcher:5001").rstrip("/")
RAW_DIR = os.environ.get("RAW_DIR", "/data/raw")
ANALYSIS_TIMEOUT_SEC = int(os.environ.get("ANALYSIS_TIMEOUT_SEC", "600"))
SKIP_ABORT = os.environ.get("SKIP_ABORT", "") == "1"

# El modelo clasifica ventanas de 30 s: con menos señal la sesión acaba en 'error'.
MIN_DURATION_SEC = 30
BUSY = ("capturing", "paused", "restarting", "uploading")


class Fail(Exception):
    pass


def ok(msg):
    print(f"  ✓ {msg}", flush=True)


def check(cond, msg):
    if not cond:
        raise Fail(msg)
    ok(msg)


class Browser:
    """Cliente Socket.IO que guarda en orden todo lo que recibe el navegador."""

    def __init__(self):
        self.sio = socketio.Client(reconnection=True)
        self.events = []
        self.cond = threading.Condition()
        for name in ("status", "countdown", "pi", "error", "aborted", "analyzing", "new"):
            self.sio.on(name, self._handler(name))

    def _handler(self, name):
        def handle(data=None):
            with self.cond:
                self.events.append((name, data or {}))
                self.cond.notify_all()
        return handle

    def connect(self):
        self.sio.connect(WEB_URL, transports=["websocket"])

    def mark(self):
        with self.cond:
            return len(self.events)

    def wait_for(self, since, timeout, pred, what):
        """Primer evento desde `since` que cumple pred(name, data); falla con `what`."""
        deadline = time.monotonic() + timeout
        i = since
        with self.cond:
            while True:
                while i < len(self.events):
                    name, data = self.events[i]
                    i += 1
                    if name == "error":
                        raise Fail(f"el servidor respondió error: {data.get('message')}")
                    if pred(name, data):
                        return i, data
                left = deadline - time.monotonic()
                if left <= 0:
                    raise Fail(f"sin {what} tras {timeout} s")
                self.cond.wait(left)

    def wait_status(self, since, values, timeout):
        values = (values,) if isinstance(values, str) else values
        return self.wait_for(
            since, timeout,
            lambda n, d: n == "status" and d.get("value") in values,
            f"status {'/'.join(values)}",
        )

    def emit(self, event):
        self.sio.emit(event, {})

    def close(self):
        self.sio.disconnect()


def get(path, base=WEB_URL, **kw):
    return requests.get(base + path, timeout=5, **kw)


def wait_services(timeout=180):
    print("1. Servicios", flush=True)
    deadline = time.monotonic() + timeout
    last = "sin respuesta"
    while time.monotonic() < deadline:
        try:
            web = get("/health").json()
            watcher = get("/health", base=WATCHER_URL).json()
            if web.get("ok") and watcher.get("ok") and web.get("pi_connected"):
                break
            last = f"servidor-web {web}, watcher {watcher}"
        except (requests.RequestException, ValueError) as exc:
            last = str(exc)
        time.sleep(2)
    else:
        raise Fail(f"servicios no listos tras {timeout} s: {last}")
    ok("servidor-web y watcher sanos (el watcher llega a PostgreSQL)")
    ok("Pi registrada en el servidor-web")
    page = get("/")
    check(page.status_code == 200 and "<html" in page.text.lower(), "la página responde en /")
    check(get("/api/sesiones").status_code == 200, "GET /api/sesiones responde")


def poll_estado(session_id, final, timeout):
    """Consulta /api/estado cada 2 s, como el navegador, hasta un estado final."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        r = get(f"/api/estado/{session_id}")
        if r.status_code == 200:
            row = r.json()
            if not seen or seen[-1] != row["status"]:
                seen.append(row["status"])
                print(f"    estado: {row['status']} ({row.get('status_detail') or '-'})", flush=True)
            if row["status"] in final:
                return row
        time.sleep(2)
    raise Fail(f"{session_id} sin llegar a {'/'.join(final)} tras {timeout} s (vista: {seen})")


def full_session(browser):
    print("2. Sesión completa", flush=True)
    since = browser.mark()
    browser.emit("start")
    since, data = browser.wait_status(since, "capturing", 15)
    session_id = data.get("session_id")
    check(bool(session_id), f"captura iniciada: {session_id}")
    since, cd = browser.wait_for(since, 10, lambda n, d: n == "countdown", "countdown")
    duration = int(cd.get("value", 0))
    if duration < MIN_DURATION_SEC:
        # Cancelar para no dejar la captura corriendo (y esperar a que la Pi
        # lo confirme: si el cliente se desconecta antes, el abort se pierde).
        browser.emit("abort")
        try:
            browser.wait_status(since, "aborted", 15)
        except Fail:
            pass
        raise Fail(
            f"la captura dura {duration} s y el modelo necesita al menos "
            f"{MIN_DURATION_SEC} s; levantar con SIM_DURATION_SEC={MIN_DURATION_SEC * 2} o más"
        )
    print(f"    esperando {duration} s de captura...", flush=True)

    # Pasa por 'uploading'; los 3 intentos de subida caben en el margen.
    since, data = browser.wait_status(since, ("done", "upload_failed"), duration + 60)
    check(data["value"] == "done", "JSONL subido al watcher con SHA-256 (status done)")
    browser.wait_for(since, 5, lambda n, d: n == "analyzing", "analyzing")
    ok("el servidor avisa 'analyzing' al navegador")

    started = time.monotonic()
    row = poll_estado(session_id, ("ready", "error"), ANALYSIS_TIMEOUT_SEC)
    check(row["status"] == "ready",
          f"sesión en 'ready' en {time.monotonic() - started:.0f} s"
          if row["status"] == "ready" else f"sesión en 'error': {row.get('status_detail')}")

    res = get(f"/api/resultados/{session_id}").json()
    m = res["metrics"] or {}
    check(m.get("bpm") is not None and 40 <= m["bpm"] <= 180,
          f"métricas de Spark: BPM {m.get('bpm')}, SDNN {m.get('sdnn')} ms, "
          f"RMSSD {m.get('rmssd')} ms, pNN50 {m.get('pnn50')}")
    check(res["summary"]["peaks"] > 0, f"{res['summary']['peaks']} picos guardados")
    windows = res["windows"]
    check(len(windows) >= 1 and all(w["level"] in ("estres", "sin_estres")
                                    and 0 <= w["score"] <= 1 for w in windows),
          f"modelo: {len(windows)} ventanas de 30 s, "
          f"{res['summary']['estres']} con estrés, nivel global {res['summary']['level']}")
    transitions = [e["to_status"] for e in res["events"]]
    check(transitions == ["uploaded", "processing", "ready"],
          f"historial de estados: {' → '.join(transitions)}")

    if os.path.isdir(RAW_DIR):
        raw = os.path.join(RAW_DIR, f"{session_id}.jsonl")
        check(not os.path.exists(raw), f"crudo borrado de {RAW_DIR} tras 'ready'")
    else:
        print(f"    ({RAW_DIR} no montado: se salta la comprobación del crudo)", flush=True)

    ids = [s["session_id"] for s in get("/api/sesiones").json()]
    check(session_id in ids, "aparece en las últimas sesiones de la bienvenida")
    browser.emit("new")
    return session_id


def cancelled_session(browser):
    print("3. Cancelación", flush=True)
    since = browser.mark()
    browser.emit("start")
    since, data = browser.wait_status(since, "capturing", 15)
    session_id = data.get("session_id")
    ok(f"captura iniciada: {session_id}")
    # Dos countdowns después del inicial: unos 2 s de captura.
    for _ in range(3):
        since, _ = browser.wait_for(since, 10, lambda n, d: n == "countdown", "countdown")
    browser.emit("abort")
    since, _ = browser.wait_status(since, "aborted", 15)
    browser.wait_for(since, 5, lambda n, d: n == "aborted", "aborted")
    ok("la Pi confirma 'aborted' y el navegador vuelve a bienvenida")

    row = poll_estado(session_id, ("error",), 15)
    check((row.get("status_detail") or "").startswith("abort"),
          f"sesión en 'error' como cancelada: {row.get('status_detail')}")
    res = get(f"/api/resultados/{session_id}").json()
    transitions = [e["to_status"] for e in res["events"]]
    check(transitions == ["created", "error"], f"historial de estados: {' → '.join(transitions)}")
    check(res["metrics"] is None and not res["windows"], "sin métricas ni ventanas")
    browser.emit("new")


def main():
    print(f"Prueba de punta a punta contra {WEB_URL} y {WATCHER_URL}", flush=True)
    browser = None
    try:
        wait_services()
        browser = Browser()
        browser.connect()
        # Al conectarse, el servidor manda el último status de la Pi.
        time.sleep(1)
        busy = [d for n, d in browser.events if n == "status" and d.get("value") in BUSY]
        if busy:
            raise Fail(f"ya hay una captura en curso ({busy[-1]}); esperar a que termine")
        full_session(browser)
        if not SKIP_ABORT:
            cancelled_session(browser)
    except Fail as exc:
        print(f"  ✗ {exc}", flush=True)
        print("FALLÓ. Ver: docker compose logs simulador-pi watcher", flush=True)
        return 1
    finally:
        if browser is not None:
            browser.close()
    print("OK: el sistema completo funciona de punta a punta.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

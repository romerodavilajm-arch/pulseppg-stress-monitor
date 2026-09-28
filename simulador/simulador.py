"""Simulador de la Pi — fases 2 a 4.

Se comporta como la Pi frente al servidor-web (docs/Propuesta técnica.md,
sección 8), pero en lugar del MAX30102 genera una señal PPG sintética:

  1. Se conecta por Socket.IO y envía `register_pi`.
  2. Espera `start`.
  3. Emite `status: capturing`, un `sample {t, ir, red}` cada 1/50 s y un
     `countdown {value}` cada segundo, durante DURATION_SEC.
  4. Escribe cada muestra en /tmp/ppg/<session_id>.jsonl (pi/writer.py).
  5. Al terminar emite `status: uploading`, sube el JSONL al watcher con
     SHA-256 y 3 intentos (pi/transfer.py) y emite `status: done`, o
     `status: upload_failed` si el watcher no confirmó (queda en pending/ y se
     reintenta antes de la siguiente sesión).
  Si llega `abort`, se detiene, borra el JSONL y emite `status: aborted`.

writer.py y transfer.py son el mismo código que usará la Pi real (carpeta pi/).

Variables de entorno:
  SERVER_URL    URL del servidor-web (por defecto http://localhost:5000)
  DURATION_SEC  duración de la sesión (por defecto 300)
  SAMPLE_RATE   muestras por segundo (por defecto 50)
  BPM           pulso medio simulado (por defecto 72)
  WATCHER_URL   URL del watcher; vacío = no subir (comportamiento de la Fase 2)
  DEVICE_ID     identificador de esta "Pi" en el session_id (por defecto sim01)
  PPG_DIR       carpeta local del crudo (por defecto /tmp/ppg)
"""

import logging
import math
import os
import random
import threading
import time
from datetime import datetime, timezone

import socketio

import transfer
from writer import SessionWriter, new_session_id

SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:5000")
DURATION_SEC = int(os.environ.get("DURATION_SEC", "300"))
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "50"))
BPM = float(os.environ.get("BPM", "72"))
WATCHER_URL = os.environ.get("WATCHER_URL", "").rstrip("/")
DEVICE_ID = os.environ.get("DEVICE_ID", "sim01")
PPG_DIR = os.environ.get("PPG_DIR", "/tmp/ppg")

sio = socketio.Client(reconnection=True, reconnection_delay=1, reconnection_delay_max=5)
capture = None  # hilo de la sesión en curso
stop = threading.Event()


def log(msg):
    print(f"[simulador] {msg}", flush=True)


def send(event, data):
    """Emite si hay conexión; si no, descarta (la Pi real sigue capturando)."""
    try:
        sio.emit(event, data)
    except socketio.exceptions.BadNamespaceError:
        pass


def pulse_shape(phase):
    """Un latido normalizado: pico sistólico + muesca dicrótica, fase en [0, 1)."""
    systolic = math.exp(-((phase - 0.15) ** 2) / (2 * 0.06 ** 2))
    dicrotic = 0.35 * math.exp(-((phase - 0.45) ** 2) / (2 * 0.05 ** 2))
    return systolic + dicrotic


def run_session():
    if WATCHER_URL:
        # Fase 8 de la propuesta: antes de capturar, limpiar y reintentar lo viejo.
        transfer.clear_completed(PPG_DIR)
        transfer.retry_pending(WATCHER_URL, PPG_DIR)

    start = datetime.now(timezone.utc)
    session_id = new_session_id(DEVICE_ID, start)
    writer = SessionWriter(PPG_DIR, session_id)
    log(f"captura {session_id} iniciada ({DURATION_SEC} s a {SAMPLE_RATE} Hz)")
    send("status", {"value": "capturing"})
    send("countdown", {"value": DURATION_SEC})

    dt = 1.0 / SAMPLE_RATE
    total = DURATION_SEC * SAMPLE_RATE
    phase = 0.0
    t0 = time.monotonic()

    for i in range(total):
        # Programar contra el reloj de inicio evita que el retraso se acumule.
        delay = t0 + i * dt - time.monotonic()
        if stop.wait(max(delay, 0)):
            writer.discard()
            log(f"captura {session_id} abortada, JSONL borrado")
            send("status", {"value": "aborted"})
            return

        t = i * dt
        # Arritmia sinusal respiratoria: el pulso oscila ±4 BPM a 0.25 Hz,
        # así la HRV de la señal simulada no es cero.
        bpm = BPM + 4 * math.sin(2 * math.pi * 0.25 * t) + random.gauss(0, 0.5)
        phase = (phase + bpm / 60 * dt) % 1.0
        wave = pulse_shape(phase)
        drift = 300 * math.sin(2 * math.pi * 0.05 * t)
        ir = 110000 + drift + 1500 * wave + random.gauss(0, 40)
        red = 80000 + 0.7 * drift + 1000 * wave + random.gauss(0, 30)

        sample = {"t": round(t, 3), "ir": int(ir), "red": int(red)}
        writer.write(**sample)
        send("sample", sample)

        if (i + 1) % SAMPLE_RATE == 0:
            send("countdown", {"value": DURATION_SEC - (i + 1) // SAMPLE_RATE})

    writer.close()
    log(f"captura {session_id} terminada: {writer.samples} muestras en {writer.path}")

    if not WATCHER_URL:
        send("status", {"value": "done"})
        return

    send("status", {"value": "uploading"})
    meta = {
        "session_id": session_id,
        "device_id": DEVICE_ID,
        "start_time": start.isoformat(),
        "duration": DURATION_SEC,
        # La validación de calidad real (dedo, movimiento) es de la Pi; el
        # simulador siempre produce una señal limpia.
        "quality": "simulada",
    }
    ok = transfer.upload(WATCHER_URL, writer.path, meta, PPG_DIR)
    send("status", {"value": "done" if ok else "upload_failed"})


@sio.event
def connect():
    log(f"conectado a {SERVER_URL}")
    sio.emit("register_pi", {})


@sio.event
def disconnect(*_):
    log("desconectado, reintentando...")


@sio.on("start")
def on_start(_=None):
    global capture
    if capture is not None and capture.is_alive():
        log("start ignorado: ya hay una captura en curso")
        return
    stop.clear()
    capture = threading.Thread(target=run_session, daemon=True)
    capture.start()


@sio.on("abort")
def on_abort(_=None):
    if capture is not None and capture.is_alive():
        stop.set()


def main():
    logging.basicConfig(level=logging.INFO, format="[simulador] %(name)s: %(message)s")
    if WATCHER_URL:
        log(f"subiendo sesiones a {WATCHER_URL}")
        transfer.retry_pending(WATCHER_URL, PPG_DIR)
    # El servidor puede tardar en arrancar: reintentar la primera conexión.
    while True:
        try:
            sio.connect(SERVER_URL, transports=["websocket"])
            break
        except socketio.exceptions.ConnectionError as exc:
            log(f"sin conexión ({exc}), reintento en 2 s")
            time.sleep(2)
    sio.wait()


if __name__ == "__main__":
    main()

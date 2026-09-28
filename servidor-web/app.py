"""servidor-web — Fase 2: puente WebSocket Pi ↔ navegador.

Protocolo (docs/Propuesta técnica.md, sección 8):

  Pi → servidor:        register_pi {}, sample {t, ir, red}, countdown {value},
                        status {value}
  servidor → Pi:        start {}, abort {}
  navegador → servidor: start {}, abort {}, new {}
  servidor → navegador: sample, countdown, status, aborted {}, error {message}

Extra de esta fase: el servidor avisa al navegador si hay una Pi conectada con
el evento `pi {connected}`, para que la página pueda deshabilitar "Comenzar".

El servidor no guarda nada. La inserción de la sesión en PostgreSQL al recibir
`start` llega en una fase posterior.
"""

import logging
import os

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

BROWSERS = "browsers"
PI = "pi"

app = Flask(__name__)
# threading + simple-websocket: sin eventlet ni gevent, suficiente para 1 Pi
# y unos pocos navegadores a 50 Hz.
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
log = logging.getLogger("servidor-web")

# sid de la Pi registrada. Solo se admite una; si se registra otra, reemplaza
# a la anterior (el caso normal es que la misma Pi se reconecte).
pi_sid = None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"ok": True, "pi_connected": pi_sid is not None}


# --- Conexión -----------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    # Todo cliente empieza como navegador; la Pi se identifica con register_pi.
    join_room(BROWSERS)
    emit("pi", {"connected": pi_sid is not None})


@socketio.on("disconnect")
def on_disconnect(*_):
    global pi_sid
    if request.sid == pi_sid:
        pi_sid = None
        log.info("Pi desconectada")
        socketio.emit("pi", {"connected": False}, to=BROWSERS)


@socketio.on("register_pi")
def on_register_pi(_=None):
    global pi_sid
    leave_room(BROWSERS)
    join_room(PI)
    pi_sid = request.sid
    log.info("Pi registrada (sid=%s)", pi_sid)
    socketio.emit("pi", {"connected": True}, to=BROWSERS)


# --- Pi → navegador -----------------------------------------------------------

def from_pi():
    return pi_sid is not None and request.sid == pi_sid


@socketio.on("sample")
def on_sample(data):
    if from_pi():
        socketio.emit("sample", data, to=BROWSERS)


@socketio.on("countdown")
def on_countdown(data):
    if from_pi():
        socketio.emit("countdown", data, to=BROWSERS)


@socketio.on("status")
def on_status(data):
    if not from_pi():
        return
    log.info("Pi status: %s", data.get("value"))
    socketio.emit("status", data, to=BROWSERS)
    if data.get("value") == "aborted":
        socketio.emit("aborted", {}, to=BROWSERS)


# --- Navegador → Pi -----------------------------------------------------------

def forward_to_pi(event):
    if pi_sid is None:
        emit("error", {"message": "No hay una Pi conectada"})
        return
    log.info("Reenviando %s a la Pi", event)
    socketio.emit(event, {}, to=pi_sid)


@socketio.on("start")
def on_start(_=None):
    forward_to_pi("start")


@socketio.on("abort")
def on_abort(_=None):
    forward_to_pi("abort")


@socketio.on("new")
def on_new(_=None):
    # Sin efecto hasta que exista la pantalla de resultados (Fase 8).
    pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Sin una línea por petición HTTP: el healthcheck las generaría cada 5 s.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = int(os.environ.get("PORT", "5000"))
    # Werkzeug basta para la red local del proyecto; no es un despliegue público.
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)

"""servidor-web — Fases 2 y 8: puente WebSocket Pi ↔ navegador y pantalla única.

Protocolo (docs/Propuesta técnica.md, sección 8):

  Pi → servidor:        register_pi {}, sample {t, ir, red}, countdown {value},
                        status {value, session_id}
  servidor → Pi:        start {}, abort {}
  navegador → servidor: start {}, abort {}, new {}
  servidor → navegador: sample, countdown, status, analyzing {session_id},
                        aborted {}, error {message}, new {}

Extra de la Fase 2: el servidor avisa al navegador si hay una Pi conectada con
el evento `pi {connected}`, para que la página pueda deshabilitar "Comenzar".

Fase 8: la página es una sola pantalla con 4 estados (bienvenida, captura,
analizando, resultados). Cuando la Pi emite `status: done` el servidor avisa
`analyzing {session_id}`; el navegador consulta GET /api/estado/<session_id>
cada 2 s y, al llegar a 'ready', pide GET /api/resultados/<session_id>.

El servidor sigue siendo pasivo (docs/Decisiones.md): solo lee PostgreSQL.
Las sesiones las crea el watcher al recibir el POST.

Fase 9: el `abort` del navegador se reenvía a la Pi; la Pi registra la
cancelación en el watcher (POST /abort, sesión en 'error') antes de emitir
`status: aborted`, y el servidor avisa `aborted` a los navegadores.
"""

import logging
import os
import threading
from datetime import datetime

import psycopg
from flask import Flask, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider
from flask_socketio import SocketIO, emit, join_room, leave_room
from psycopg.rows import dict_row

BROWSERS = "browsers"
PI = "pi"

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://pulseppg:pulseppg@postgres:5432/pulseppg"
)
# Estado global de la sesión según la fracción de ventanas clasificadas como
# estrés (docs/Decisiones.md): < 1/3 bajo, < 2/3 moderado, si no alto.
LEVELS = ((1 / 3, "bajo"), (2 / 3, "moderado"), (1.01, "alto"))

class JSONProvider(DefaultJSONProvider):
    """Fechas en ISO 8601 (con zona) en lugar del formato HTTP de Flask."""

    ensure_ascii = False

    @staticmethod
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


app = Flask(__name__)
app.json = JSONProvider(app)
# threading + simple-websocket: sin eventlet ni gevent, suficiente para 1 Pi
# y unos pocos navegadores a 50 Hz.
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
log = logging.getLogger("servidor-web")

# sid de la Pi registrada. Solo se admite una; si se registra otra, reemplaza
# a la anterior (el caso normal es que la misma Pi se reconecte).
pi_sid = None

# Último `status` de la Pi, para que un navegador que se conecta (o recarga)
# a mitad de una captura o de un análisis caiga en la pantalla correcta.
# `new` lo borra y todos vuelven a bienvenida.
last_status = None
state_lock = threading.Lock()
BUSY = ("capturing", "paused", "restarting", "uploading")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=3)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"ok": True, "pi_connected": pi_sid is not None}


# --- API de consulta ----------------------------------------------------------

@app.get("/api/estado/<session_id>")
def api_estado(session_id):
    with db() as conn:
        row = conn.execute(
            "SELECT session_id, status, status_detail, updated_at"
            " FROM sessions WHERE session_id = %s",
            (session_id,),
        ).fetchone()
    if row is None:
        # Normal justo después de `done` si el navegador pregunta antes de que
        # el watcher confirme; el navegador sigue consultando.
        return jsonify({"session_id": session_id, "status": None}), 404
    return jsonify(row)


def global_level(windows):
    if not windows:
        return None
    fraction = sum(w["level"] == "estres" for w in windows) / len(windows)
    return next(name for limit, name in LEVELS if fraction < limit)


@app.get("/api/resultados/<session_id>")
def api_resultados(session_id):
    with db() as conn:
        session = conn.execute(
            "SELECT id, session_id, device_id, start_time, duration_sec, quality,"
            " checksum, status, status_detail, updated_at"
            " FROM sessions WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        if session is None:
            return jsonify({"error": "sesión inexistente"}), 404
        pk = session.pop("id")
        metrics = conn.execute(
            "SELECT bpm, sdnn, rmssd, pnn50 FROM metrics WHERE session_pk = %s", (pk,)
        ).fetchone()
        windows = conn.execute(
            "SELECT t_start, t_end, level, score FROM stress_windows"
            " WHERE session_pk = %s ORDER BY t_start",
            (pk,),
        ).fetchall()
        peaks = conn.execute(
            "SELECT count(*) AS n FROM peaks WHERE session_pk = %s", (pk,)
        ).fetchone()["n"]
        events = conn.execute(
            "SELECT from_status, to_status, detail, changed_at FROM session_events"
            " WHERE session_pk = %s ORDER BY changed_at, id",
            (pk,),
        ).fetchall()

    stressed = sum(w["level"] == "estres" for w in windows)
    return jsonify({
        "session": session,
        "metrics": metrics,
        "windows": windows,
        "summary": {
            "level": global_level(windows),
            "windows": len(windows),
            "estres": stressed,
            "peaks": peaks,
        },
        "events": events,
    })


@app.get("/api/sesiones")
def api_sesiones():
    with db() as conn:
        rows = conn.execute(
            "SELECT s.session_id, s.start_time, s.status, s.status_detail, m.bpm"
            " FROM sessions s LEFT JOIN metrics m ON m.session_pk = s.id"
            " ORDER BY s.start_time DESC LIMIT 10"
        ).fetchall()
    return jsonify(rows)


@app.errorhandler(psycopg.OperationalError)
def db_unavailable(exc):
    log.warning("PostgreSQL no disponible: %s", exc)
    return jsonify({"error": "base de datos no disponible"}), 503


# --- Conexión -----------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    # Todo cliente empieza como navegador; la Pi se identifica con register_pi.
    join_room(BROWSERS)
    emit("pi", {"connected": pi_sid is not None})
    with state_lock:
        current = last_status
    if current is not None:
        emit("status", current)
        if current.get("value") == "done":
            emit("analyzing", {"session_id": current.get("session_id")})


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
    global last_status
    if not from_pi():
        return
    value = data.get("value")
    log.info("Pi status: %s (%s)", value, data.get("session_id", "-"))
    with state_lock:
        # Tras un abort no hay nada que recordar: se vuelve a bienvenida.
        last_status = None if value == "aborted" else data
    socketio.emit("status", data, to=BROWSERS)
    if value == "aborted":
        socketio.emit("aborted", {}, to=BROWSERS)
    elif value == "done":
        socketio.emit("analyzing", {"session_id": data.get("session_id")}, to=BROWSERS)


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
    # "Nueva sesión": todos los navegadores vuelven a bienvenida. La Pi limpia
    # completed/ y reintenta pending/ al recibir el siguiente start.
    # Si la Pi está capturando o subiendo (alguien pulsó "Nueva sesión" viendo
    # una sesión anterior), solo ese navegador vuelve a la captura en curso.
    global last_status
    with state_lock:
        current = last_status
        busy = current is not None and current.get("value") in BUSY
        if not busy:
            last_status = None
    if busy:
        emit("new", {})
        emit("status", current)
    else:
        socketio.emit("new", {}, to=BROWSERS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Sin una línea por petición HTTP: el healthcheck y el polling la generarían
    # cada pocos segundos.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = int(os.environ.get("PORT", "5000"))
    # Werkzeug basta para la red local del proyecto; no es un despliegue público.
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)

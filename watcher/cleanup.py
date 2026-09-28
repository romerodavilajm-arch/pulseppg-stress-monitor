"""Limpieza (Fase 10): borrado del crudo y retención de 10 sesiones.

docs/Propuesta técnica.md, flujo "Limpieza":

  1. Cuando una sesión llega a 'ready', se borra /data/raw/<session_id>.jsonl.
     Si termina en 'error' el crudo se conserva para poder revisarlo.
  2. Después de cada sesión que termina (ready, error o abort) se borran las
     sesiones que quedan fuera de las 10 más recientes por start_time. Solo
     se borran las terminadas ('ready' o 'error'): una que aún se procesa no
     se toca, ni la sesión que acaba de terminar aunque su start_time sea
     viejo (un reintento desde pending/ de la Pi). ON DELETE CASCADE se
     lleva metrics, peaks, stress_windows y session_events; su crudo, si
     quedaba, también se borra.
  3. Al arrancar el watcher se barre lo que haya quedado a medias: los .part
     de .incoming/ (ningún upload está en curso todavía), el crudo de sesiones
     en 'ready' y el de archivos sin sesión en la base.

La propuesta solo borraba sesiones en 'ready'; aquí también las de 'error',
porque las capturas canceladas (Fase 9) quedan en 'error' y si no se
acumularían sin límite.
"""

import logging
import os

import psycopg

log = logging.getLogger("watcher.cleanup")

RAW_DIR = os.environ.get("RAW_DIR", "/data/raw")
INCOMING_DIR = os.path.join(RAW_DIR, ".incoming")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://pulseppg:pulseppg@postgres:5432/pulseppg"
)
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "10"))


def raw_path(session_id):
    return os.path.join(RAW_DIR, f"{session_id}.jsonl")


def delete_raw(session_id):
    """Borra el crudo de la sesión si existe. Devuelve True si borró algo."""
    try:
        os.remove(raw_path(session_id))
    except FileNotFoundError:
        return False
    log.info("%s: crudo borrado", session_id)
    return True


def purge_old_sessions(keep_pk=None):
    """Deja solo las MAX_SESSIONS más recientes; devuelve los session_id borrados.

    keep_pk es la sesión que acaba de terminar: no se borra aunque sea vieja,
    así el navegador que la está esperando puede ver sus resultados.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            """
            DELETE FROM sessions
            WHERE id NOT IN (
                SELECT id FROM sessions ORDER BY start_time DESC, id DESC LIMIT %s
            )
            AND status IN ('ready', 'error')
            AND id IS DISTINCT FROM %s
            RETURNING session_id
            """,
            (MAX_SESSIONS, keep_pk),
        ).fetchall()
    removed = [r[0] for r in rows]
    for session_id in removed:
        delete_raw(session_id)
    if removed:
        log.info("retención: %d sesión(es) viejas borradas: %s",
                 len(removed), ", ".join(removed))
    return removed


def after_session(keep_pk=None):
    """Retención tras cada sesión que termina; un fallo solo se registra."""
    try:
        purge_old_sessions(keep_pk)
    except Exception:
        log.exception("retención: no se pudieron borrar las sesiones viejas")


def sweep_on_start():
    """Al arrancar, antes de aceptar uploads: borra lo que quedó a medias."""
    os.makedirs(INCOMING_DIR, exist_ok=True)
    for name in os.listdir(INCOMING_DIR):
        os.remove(os.path.join(INCOMING_DIR, name))
        log.info("barrido: temporal %s borrado", name)

    with psycopg.connect(DATABASE_URL) as conn:
        status = dict(conn.execute("SELECT session_id, status FROM sessions").fetchall())
    for name in sorted(os.listdir(RAW_DIR)):
        if name.startswith(".") or not name.endswith(".jsonl"):
            continue
        session_id = name[: -len(".jsonl")]
        if session_id not in status:
            os.remove(os.path.join(RAW_DIR, name))
            log.info("barrido: %s sin sesión en la base, borrado", name)
        elif status[session_id] == "ready":
            delete_raw(session_id)

    purge_old_sessions()

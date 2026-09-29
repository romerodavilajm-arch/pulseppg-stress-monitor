"""Lanzamiento de los contenedores de análisis (Spark y modelo).

Por cada archivo nuevo en /data/raw/ (docs/Propuesta técnica.md, flujo de la
Fase 5):

  1. Pasa la sesión de 'uploaded' a 'processing'.
  2. Lanza, uno tras otro, los contenedores efímeros de ANALYSIS_STEPS con el
     volumen rawdata en solo lectura y en la misma red que PostgreSQL:
       spark:  python3 /app/hrv.py /data/raw/<session_id>.jsonl <session_pk>
       modelo: python3 /app/infer.py /data/raw/<session_id>.jsonl <session_pk>
     status_detail dice qué paso está corriendo.
  3. Si un paso falla, espera 3 s y lo reintenta una vez; si vuelve a fallar,
     la sesión pasa a 'error' con el motivo, el archivo se conserva y no se
     corren los pasos siguientes.
  4. Si todos salen bien, la sesión pasa a 'ready' y se borra el crudo.
  5. Termine como termine, se aplica la retención de 10 sesiones
     (cleanup.py, Fase 10).

Las sesiones se procesan de una en una, en el orden en que llegan.
"""

import logging
import os
import queue
import socket
import threading
import time

import docker
import psycopg

import cleanup

log = logging.getLogger("watcher.runner")

RAW_DIR = os.environ.get("RAW_DIR", "/data/raw")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://pulseppg:pulseppg@postgres:5432/pulseppg"
)
SPARK_IMAGE = os.environ.get("SPARK_IMAGE", "pulseppg-spark")
MODELO_IMAGE = os.environ.get("MODELO_IMAGE", "pulseppg-modelo")
STEP_TIMEOUT_SEC = int(os.environ.get("STEP_TIMEOUT_SEC", "300"))
RETRY_DELAY_SEC = 3

# (nombre, imagen, comando). {path} y {pk} se sustituyen por sesión.
ANALYSIS_STEPS = [
    ("spark", SPARK_IMAGE, ["python3", "/app/hrv.py", "{path}", "{pk}"]),
    ("modelo", MODELO_IMAGE, ["python3", "/app/infer.py", "{path}", "{pk}"]),
]


class Runner:
    def __init__(self):
        self._queue = queue.Queue()
        self._docker = docker.from_env()
        self._network, self._raw_volume = self._own_network_and_volume()

    def _own_network_and_volume(self):
        """Red y volumen del propio watcher, para montarlos igual en cada paso.

        Así no se fija el nombre del proyecto de compose (pulseppg_default,
        pulseppg_rawdata) en el código.
        """
        me = self._docker.containers.get(socket.gethostname())
        network = next(iter(me.attrs["NetworkSettings"]["Networks"]))
        volume = next(
            m["Name"] for m in me.attrs["Mounts"]
            if m["Destination"] == RAW_DIR and m["Type"] == "volume"
        )
        log.info("análisis en la red %s con el volumen %s", network, volume)
        return network, volume

    def start(self):
        threading.Thread(target=self._loop, name="runner", daemon=True).start()

    def submit(self, session_id):
        self._queue.put(session_id)

    def recover(self):
        """Al arrancar: retoma lo que quedó en 'uploaded' o 'processing'."""
        with psycopg.connect(DATABASE_URL) as conn:
            rows = conn.execute(
                "SELECT session_id, status FROM sessions"
                " WHERE status IN ('uploaded', 'processing') ORDER BY start_time"
            ).fetchall()
        for session_id, status in rows:
            log.info("recuperación: %s estaba en '%s'", session_id, status)
            self.submit(session_id)

    def _loop(self):
        while True:
            session_id = self._queue.get()
            pk = None
            try:
                pk = self.process(session_id)
            except Exception:
                log.exception("%s: error inesperado del runner", session_id)
            cleanup.after_session(keep_pk=pk)

    def _claim(self, session_id):
        """Pasa la sesión a 'processing' y devuelve su pk, o None si no toca.

        'processing' también se acepta (repetir el estado es un no-op en el
        trigger): es una sesión que quedó a medias porque el watcher se cayó.
        El watcher mueve el archivo a /data/raw/ justo antes de confirmar la
        transacción, así que el watchdog puede verlo unos milisegundos antes
        que la fila: si la sesión aún no está en 'uploaded' se espera un poco.
        """
        for _ in range(20):
            with psycopg.connect(DATABASE_URL) as conn:
                row = conn.execute(
                    """
                    UPDATE sessions SET status = 'processing', status_detail = %s
                    WHERE session_id = %s AND status IN ('uploaded', 'processing')
                    RETURNING id
                    """,
                    (ANALYSIS_STEPS[0][0], session_id),
                ).fetchone()
                if row:
                    return row[0]
                current = conn.execute(
                    "SELECT status FROM sessions WHERE session_id = %s", (session_id,)
                ).fetchone()
            if current is not None and current[0] != "created":
                log.info("%s: ya está en '%s', no se procesa", session_id, current[0])
                return None
            time.sleep(0.25)
        log.warning("%s: archivo sin sesión en 'uploaded', se ignora", session_id)
        return None

    def _set_status(self, pk, status, detail):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "UPDATE sessions SET status = %s, status_detail = %s WHERE id = %s",
                (status, detail, pk),
            )

    def process(self, session_id):
        """Analiza la sesión y devuelve su pk (None si no le tocaba)."""
        pk = self._claim(session_id)
        if pk is None:
            return None
        path = os.path.join(RAW_DIR, f"{session_id}.jsonl")
        if not os.path.isfile(path):
            self._set_status(pk, "error", f"no se encontró {path}")
            log.error("%s: no se encontró %s, sesión en 'error'", session_id, path)
            return pk
        log.info("%s: 'processing' (pk=%d)", session_id, pk)

        for i, (name, image, command) in enumerate(ANALYSIS_STEPS):
            if i > 0:
                self._set_status(pk, "processing", name)
            argv = [arg.format(path=path, pk=pk) for arg in command]
            for attempt in (1, 2):
                ok, detail = self._run(name, image, argv, pk, attempt)
                if ok:
                    break
                if attempt == 1:
                    log.warning("%s: %s falló (%s), reintento en %d s",
                                session_id, name, detail, RETRY_DELAY_SEC)
                    time.sleep(RETRY_DELAY_SEC)
            else:
                self._set_status(pk, "error", f"{name}: {detail}"[:1000])
                log.error("%s: %s falló dos veces, sesión en 'error'; se conserva %s",
                          session_id, name, path)
                return pk

        self._set_status(pk, "ready", "análisis OK")
        log.info("%s: 'ready'", session_id)
        # Los resultados ya están en la base; el respaldo vive en la Pi.
        cleanup.delete_raw(session_id)
        return pk

    def _run(self, name, image, argv, pk, attempt):
        """Corre un contenedor efímero y devuelve (ok, detalle)."""
        started = time.monotonic()
        try:
            container = self._docker.containers.run(
                image,
                argv,
                name=f"pulseppg-{name}-{pk}-{attempt}-{int(time.time())}",
                detach=True,
                network=self._network,
                volumes={self._raw_volume: {"bind": RAW_DIR, "mode": "ro"}},
                environment={"DATABASE_URL": DATABASE_URL},
                labels={"pulseppg.step": name, "pulseppg.session_pk": str(pk)},
            )
        except docker.errors.DockerException as exc:
            return False, f"no se pudo lanzar: {exc}"
        try:
            try:
                code = container.wait(timeout=STEP_TIMEOUT_SEC)["StatusCode"]
            except Exception:  # timeout de la API de Docker
                container.kill()
                return False, f"sin terminar tras {STEP_TIMEOUT_SEC} s"
            output = container.logs().decode(errors="replace").strip().splitlines()
            # Solo las líneas propias del paso; el resto es ruido (arranque de Spark).
            ours = [line for line in output if line.startswith(f"[{name}]")] or output[-5:]
            for line in ours:
                log.info("  %s", line)
            elapsed = time.monotonic() - started
            if code == 0:
                log.info("%s terminó en %.1f s (intento %d)", name, elapsed, attempt)
                return True, None
            return False, f"código {code}: {ours[-1] if ours else 'sin salida'}"
        finally:
            container.remove(force=True)

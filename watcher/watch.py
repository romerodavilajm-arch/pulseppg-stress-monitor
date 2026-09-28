"""Vigilancia de /data/raw/ con watchdog (Fase 5).

Cada <session_id>.jsonl que aparece en /data/raw/ se entrega al runner. Se
llama watch.py y no watchdog.py para no tapar la librería watchdog.

POST /upload escribe en /data/raw/.incoming/ y luego hace un rename atómico a
/data/raw/, así que aquí solo llegan archivos completos. Ese rename llega como
"moved" (o "created" si inotify no empareja el origen); los dos se atienden.
Se ignoran los ocultos (.incoming/ y sus .part) y lo que no sea .jsonl.
"""

import logging
import os

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger("watcher.watch")


class RawDirHandler(FileSystemEventHandler):
    def __init__(self, submit):
        self._submit = submit

    def _maybe_submit(self, path):
        name = os.path.basename(path)
        if name.startswith(".") or not name.endswith(".jsonl"):
            return
        session_id = name[: -len(".jsonl")]
        log.info("archivo nuevo en /data/raw/: %s", name)
        self._submit(session_id)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_submit(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe_submit(event.dest_path)


def watch(raw_dir, submit):
    """Arranca el observer (hilo propio) sobre raw_dir, sin subcarpetas."""
    observer = Observer()
    observer.schedule(RawDirHandler(submit), raw_dir, recursive=False)
    observer.daemon = True
    observer.start()
    log.info("vigilando %s", raw_dir)
    return observer

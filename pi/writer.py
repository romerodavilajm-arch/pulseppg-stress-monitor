"""Escritura del archivo crudo JSONL (docs/Propuesta técnica.md, sección 10).

Una línea {"t", "ir", "red"} por muestra. No depende del sensor: lo usan igual
la Pi real y el simulador.
"""

import json
import os
from datetime import datetime, timezone


def new_session_id(device_id, start=None):
    """<timestamp>_<device_id>, ej. 20260928T071500Z_pi01 (pregunta abierta 1)."""
    start = start or datetime.now(timezone.utc)
    return f"{start.strftime('%Y%m%dT%H%M%SZ')}_{device_id}"


class SessionWriter:
    def __init__(self, base_dir, session_id):
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(base_dir, f"{session_id}.jsonl")
        self._f = open(self.path, "w", buffering=64 * 1024)
        self.samples = 0

    def write(self, t, ir, red):
        self._f.write(json.dumps({"t": t, "ir": ir, "red": red}, separators=(",", ":")) + "\n")
        self.samples += 1

    def close(self):
        if not self._f.closed:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()

    def discard(self):
        """Abort: el archivo no se transfiere ni se analiza."""
        self.close()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

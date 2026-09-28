"""modelo — Fase 6: nivel de estrés por ventana con Pulse-PPG.

Uso (lo lanza el watcher después de Spark; también se puede correr a mano):

  python3 /app/infer.py /data/raw/<session_id>.jsonl <session_pk>
  python3 /app/infer.py /data/raw/<session_id>.jsonl --no-db   # solo imprime
  python3 /app/infer.py --check     # comprobación de la imagen (compose up)

Pasos (docs/Propuesta técnica.md, flujo de la Fase 5, paso "Modelo"):

  1. Lee el JSONL {"t", "ir", "red"} y toma la señal IR.
  2. Remuestrea a 50 Hz, filtra 0.5–10 Hz y corta ventanas contiguas de 30 s
     (encoder.py; la duración sale del clasificador para que coincida con la
     del entrenamiento).
  3. El encoder Pulse-PPG convierte cada ventana en un embedding de 512
     valores. Pulse-PPG no clasifica estrés por sí solo.
  4. El clasificador entrenado con train.py (StandardScaler + regresión
     logística sobre esos embeddings) da la probabilidad de estrés:
       level = 'estres' si la probabilidad ≥ 0.5, si no 'sin_estres'
       score = probabilidad de estrés (0–1)
  5. Reemplaza en una transacción las filas de la sesión en `stress_windows`.
     Reintentar no duplica nada.

No cambia el estado de la sesión: eso lo hace el watcher según el código de
salida.

Códigos de salida:
  0  ventanas escritas (o impresas con --no-db)
  1  error inesperado (pesos ilegibles, base de datos)
  2  entrada inválida o falta el clasificador: archivo sin muestras, sesión
     más corta que una ventana, sesión inexistente, clasificador sin entrenar

Variables de entorno:
  DATABASE_URL       conexión a PostgreSQL
  CLASSIFIER_PATH    clasificador (por defecto /app/artefactos/clasificador.joblib)
  PULSEPPG_WEIGHTS   checkpoint del encoder, si no se encuentra solo en /opt/pulseppg
"""

import argparse
import json
import os
import sys

import numpy as np

import encoder

CLASSIFIER_PATH = os.environ.get("CLASSIFIER_PATH", "/app/artefactos/clasificador.joblib")
STRESS_THRESHOLD = 0.5


class BadInput(Exception):
    """Entrada que ningún reintento va a arreglar (código de salida 2)."""


def log(msg, err=False):
    print(f"[modelo] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def load_classifier(path=CLASSIFIER_PATH):
    if not os.path.isfile(path):
        raise BadInput(
            f"falta el clasificador {path}: entrenarlo una vez con train.py "
            "(ver modelo/TESTING.md, sección 1)"
        )
    import joblib

    return joblib.load(path)


def read_ir(path):
    """(t, ir) del JSONL; ignora líneas mal formadas como Spark (DROPMALFORMED)."""
    t, ir = [], []
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
                t.append(float(row["t"]))
                ir.append(float(row["ir"]))
            except (ValueError, KeyError, TypeError):
                continue
    return np.array(t), np.array(ir)


def compute(path, net, clf):
    """Devuelve (ventanas [(t_start, t_end, level, score)], resumen dict)."""
    window_sec = clf["window_sec"]
    t, ir = read_ir(path)
    if len(t) < 2 or t.max() <= t.min():
        raise BadInput(f"{path}: sin muestras suficientes ({len(t)})")
    duration = t.max() - t.min()
    if duration < window_sec:
        raise BadInput(f"{path}: {duration:.1f} s de señal, menos que una ventana de {window_sec} s")

    grid, x = encoder.preprocess(t, ir, fs=clf["fs"])
    starts, batch = encoder.windows(grid, x, window_sec, fs=clf["fs"])
    if len(starts) == 0:
        raise BadInput(f"{path}: ninguna ventana completa de {window_sec} s")

    emb = encoder.embed(net, batch)
    pipeline = clf["pipeline"]
    stress_col = list(pipeline.classes_).index(1)
    proba = pipeline.predict_proba(emb)[:, stress_col]

    rows = [
        (float(s), float(s + window_sec),
         "estres" if p >= STRESS_THRESHOLD else "sin_estres", float(p))
        for s, p in zip(starts, proba)
    ]
    summary = {
        "samples": int(len(t)),
        "duration_sec": round(float(duration), 3),
        "windows": len(rows),
        "window_sec": window_sec,
        "estres": sum(r[2] == "estres" for r in rows),
        "score_mean": round(float(np.mean(proba)), 3),
    }
    return rows, summary


def save(database_url, session_pk, rows):
    import psycopg

    with psycopg.connect(database_url) as conn, conn.transaction():
        if conn.execute("SELECT 1 FROM sessions WHERE id = %s", (session_pk,)).fetchone() is None:
            raise BadInput(f"no existe la sesión con id {session_pk}")
        # Reemplazar en lugar de acumular: un reintento deja el mismo resultado.
        conn.execute("DELETE FROM stress_windows WHERE session_pk = %s", (session_pk,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stress_windows (session_pk, t_start, t_end, level, score)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(session_pk, *row) for row in rows],
            )


def check():
    """Comprobación de "compose up": los pesos cargan; avisa si falta el clasificador."""
    net, path, digest = encoder.load_encoder()
    log(f"imagen lista, encoder Pulse-PPG {os.path.relpath(path, encoder.WEIGHTS_DIR)} "
        f"(sha256 {digest[:12]}, salida {net.out_dim})")
    if os.path.isfile(CLASSIFIER_PATH):
        clf = load_classifier()
        log(f"clasificador: ventanas de {clf['window_sec']} s, "
            f"F1 macro por sujeto {clf.get('cv_f1_macro', '?')}")
        if clf.get("weights_sha256") not in (None, digest):
            log("aviso: el clasificador se entrenó con otros pesos del encoder", err=True)
    else:
        # No se falla aquí: el sistema levanta y Spark funciona; cada sesión
        # quedará en 'error' con este mismo motivo hasta entrenar.
        log(f"aviso: falta {CLASSIFIER_PATH}; entrenar con train.py", err=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Nivel de estrés por ventana de un JSONL")
    parser.add_argument("path", nargs="?", help="archivo crudo /data/raw/<session_id>.jsonl")
    parser.add_argument("session_pk", nargs="?", type=int, help="sessions.id")
    parser.add_argument("--no-db", action="store_true", help="solo imprimir, no escribir")
    parser.add_argument("--check", action="store_true", help="solo comprobar la imagen")
    args = parser.parse_args()
    if args.check:
        return check()
    if args.path is None:
        parser.error("falta el archivo")
    if args.session_pk is None and not args.no_db:
        parser.error("falta session_pk (o usar --no-db)")
    if not os.path.isfile(args.path):
        log(f"no existe {args.path}", err=True)
        return 2

    try:
        clf = load_classifier()
        net, _, digest = encoder.load_encoder()
        if clf.get("weights_sha256") not in (None, digest):
            log("aviso: el clasificador se entrenó con otros pesos del encoder", err=True)
        rows, summary = compute(args.path, net, clf)
        if not args.no_db:
            save(os.environ["DATABASE_URL"], args.session_pk, rows)
    except BadInput as exc:
        log(str(exc), err=True)
        return 2

    log(json.dumps(summary))
    if args.no_db:
        for start, end, level, score in rows:
            log(f"  {start:7.1f}–{end:7.1f} s  {level:<10} {score:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""spark — Fase 5: picos y métricas HRV de una sesión.

Uso (lo lanza el watcher; también se puede correr a mano):

  python3 /app/hrv.py /data/raw/<session_id>.jsonl <session_pk>
  python3 /app/hrv.py /data/raw/<session_id>.jsonl --no-db   # solo imprime

Pasos (docs/Propuesta técnica.md, flujo de la Fase 5):

  1. Lee el JSONL {"t", "ir", "red"} con spark.read.json().
  2. Detecta los picos sistólicos en la señal IR con funciones de ventana:
       · quita la línea base restando la media móvil de ~1 s,
       · suaviza con la media móvil de ~0.1 s,
       · un pico es el máximo de su vecindad de ±0.3 s (tope de 200 BPM) y
         queda por encima de la línea base.
  3. Intervalos RR entre picos consecutivos; se descartan los que caen fuera
     de 300–2000 ms (30–200 BPM) antes de calcular las métricas.
  4. BPM = 60000 / media(RR), SDNN = desviación estándar de RR,
     RMSSD = raíz de la media de las diferencias sucesivas al cuadrado,
     pNN50 = % de diferencias sucesivas mayores que 50 ms.
  5. Escribe en una sola transacción: reemplaza los picos de la sesión en
     `peaks` y hace upsert en `metrics`. Reintentar no duplica nada.

No cambia el estado de la sesión: eso lo hace el watcher según el código de
salida.

Códigos de salida:
  0  métricas escritas (o impresas con --no-db)
  1  error inesperado (Spark, base de datos)
  2  entrada inválida: archivo sin muestras, señal sin latidos suficientes o
     sesión inexistente

Variables de entorno:
  DATABASE_URL  conexión a PostgreSQL
"""

import argparse
import json
import os
import sys

REFRACTORY_SEC = 0.3      # dos picos no pueden estar más cerca (200 BPM)
BASELINE_SEC = 1.0        # ventana de la media móvil que se resta
SMOOTH_SEC = 0.1          # ventana del suavizado
RR_MIN_MS, RR_MAX_MS = 300.0, 2000.0
MIN_RR = 10               # menos latidos válidos que esto no es una medición


class BadInput(Exception):
    """Entrada que ningún reintento va a arreglar (código de salida 2)."""


def compute(spark, path):
    """Devuelve (picos [(t, rr_ms | None)], métricas dict, resumen dict)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    samples = (
        spark.read.schema("t DOUBLE, ir DOUBLE, red DOUBLE")
        .option("mode", "DROPMALFORMED")
        .json(path)
        .where(F.col("t").isNotNull() & F.col("ir").isNotNull())
    )
    stats = samples.agg(
        F.count("*").alias("n"), F.min("t").alias("t0"), F.max("t").alias("t1")
    ).first()
    n, t0, t1 = stats["n"], stats["t0"], stats["t1"]
    if n < 2 or t1 <= t0:
        raise BadInput(f"{path}: sin muestras suficientes ({n})")
    fs = (n - 1) / (t1 - t0)

    def half(sec):
        return max(1, int(round(sec * fs / 2)))

    # Una sola sesión (~15 000 filas): una partición ordenada por t basta.
    ordered = Window.orderBy("t")

    def around(k):
        return ordered.rowsBetween(-k, k)

    signal = (
        samples.withColumn(
            "ac", F.col("ir") - F.avg("ir").over(around(half(BASELINE_SEC)))
        )
        .withColumn("x", F.avg("ac").over(around(half(SMOOTH_SEC))))
        .withColumn("local_max", F.max("x").over(around(int(round(REFRACTORY_SEC * fs)))))
    )
    # Los bordes tienen la media móvil sesgada: se ignora medio segundo de cada lado.
    edge = BASELINE_SEC / 2
    peaks = signal.where(
        (F.col("x") == F.col("local_max"))
        & (F.col("x") > 0)
        & F.col("t").between(t0 + edge, t1 - edge)
    ).select("t")

    rr = (F.col("t") - F.lag("t").over(ordered)) * 1000
    peaks = (
        peaks.withColumn("rr_raw", rr)
        .withColumn(
            "rr",
            F.when(F.col("rr_raw").between(RR_MIN_MS, RR_MAX_MS), F.col("rr_raw")),
        )
        # Diferencia sucesiva solo entre dos RR válidos seguidos.
        .withColumn("drr", F.col("rr") - F.lag("rr").over(ordered))
    )
    m = peaks.agg(
        F.count("*").alias("peaks"),
        F.count("rr").alias("rr_n"),
        F.avg("rr").alias("rr_mean"),
        F.stddev_samp("rr").alias("sdnn"),
        F.sqrt(F.avg(F.col("drr") ** 2)).alias("rmssd"),
        (100 * F.avg((F.abs("drr") > 50).cast("double"))).alias("pnn50"),
    ).first()
    if m["rr_n"] < MIN_RR:
        raise BadInput(
            f"{path}: solo {m['rr_n']} intervalos RR válidos en {t1 - t0:.1f} s "
            f"({m['peaks']} picos); se necesitan {MIN_RR}"
        )

    rows = [(r["t"], r["rr"]) for r in peaks.select("t", "rr").orderBy("t").collect()]
    metrics = {
        "bpm": 60000.0 / m["rr_mean"],
        "sdnn": m["sdnn"],
        "rmssd": m["rmssd"],
        "pnn50": m["pnn50"],
    }
    summary = {
        "samples": n,
        "duration_sec": round(t1 - t0, 3),
        "fs_hz": round(fs, 2),
        "peaks": m["peaks"],
        "rr_valid": m["rr_n"],
    }
    return rows, metrics, summary


def save(database_url, session_pk, peaks, metrics):
    import psycopg

    with psycopg.connect(database_url) as conn, conn.transaction():
        if conn.execute("SELECT 1 FROM sessions WHERE id = %s", (session_pk,)).fetchone() is None:
            raise BadInput(f"no existe la sesión con id {session_pk}")
        # Reemplazar en lugar de acumular: un reintento deja el mismo resultado.
        conn.execute("DELETE FROM peaks WHERE session_pk = %s", (session_pk,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO peaks (session_pk, t, rr_interval) VALUES (%s, %s, %s)",
                [(session_pk, t, rr) for t, rr in peaks],
            )
        conn.execute(
            """
            INSERT INTO metrics (session_pk, bpm, sdnn, rmssd, pnn50)
            VALUES (%(pk)s, %(bpm)s, %(sdnn)s, %(rmssd)s, %(pnn50)s)
            ON CONFLICT (session_pk) DO UPDATE
            SET bpm = EXCLUDED.bpm, sdnn = EXCLUDED.sdnn, rmssd = EXCLUDED.rmssd,
                pnn50 = EXCLUDED.pnn50, computed_at = now()
            """,
            {"pk": session_pk, **metrics},
        )


def main():
    parser = argparse.ArgumentParser(description="Picos y métricas HRV de un JSONL")
    parser.add_argument("path", help="archivo crudo /data/raw/<session_id>.jsonl")
    parser.add_argument("session_pk", nargs="?", type=int, help="sessions.id")
    parser.add_argument("--no-db", action="store_true", help="solo imprimir, no escribir")
    args = parser.parse_args()
    if args.session_pk is None and not args.no_db:
        parser.error("falta session_pk (o usar --no-db)")
    if not os.path.isfile(args.path):
        print(f"[spark] no existe {args.path}", file=sys.stderr)
        return 2

    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.master("local[*]")
        .appName("pulseppg-hrv")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    try:
        peaks, metrics, summary = compute(spark, args.path)
        if not args.no_db:
            save(os.environ["DATABASE_URL"], args.session_pk, peaks, metrics)
    except BadInput as exc:
        print(f"[spark] {exc}", file=sys.stderr)
        return 2
    finally:
        spark.stop()

    rounded = {k: None if v is None else round(v, 2) for k, v in metrics.items()}
    print(f"[spark] {json.dumps({**summary, **rounded})}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

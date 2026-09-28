"""modelo — entrenamiento único del clasificador de estrés sobre WESAD.

Pulse-PPG entrega embeddings, no etiquetas. Este script entrena, una sola vez,
el clasificador lineal que los convierte en estrés / sin estrés, igual que la
evaluación "WESAD | Stress (2) | Linear Probe" del repositorio de Pulse-PPG
pero con ventanas de 30 s (las de `stress_windows`). El encoder no se toca.

Uso (dentro de la imagen del modelo; ver modelo/TESTING.md, sección 1):

  python3 /app/train.py /data/wesad [--out /app/artefactos/clasificador.joblib]

/data/wesad es la carpeta de WESAD con S2/S2.pkl … S17/S17.pkl (también sirve
la carpeta que la contiene). WESAD: Schmidt et al., ICMI 2018; licencia solo
para uso académico no comercial.

Pasos:
  1. Por sujeto: BVP de la muñeca (Empatica E4, 64 Hz) y etiquetas del
     protocolo (700 Hz). Clases: 2 = estrés (TSST) → 1; 1 = base y
     3 = diversión → 0. El resto de etiquetas (transiciones, meditación) se
     descarta.
  2. Mismo preprocesado que infer.py (encoder.preprocess y encoder.windows):
     50 Hz, 0.5–10 Hz, ventanas contiguas de 30 s. Una ventana se queda si al
     menos el 90 % de sus etiquetas son de una sola clase válida.
  3. Embeddings con el encoder Pulse-PPG.
  4. StandardScaler + regresión logística (clases balanceadas). C se elige por
     validación cruzada dejando sujetos fuera (GroupKFold), así la métrica
     reportada es para personas que el clasificador no vio.
  5. Reentrena con todos los sujetos y guarda un joblib con el pipeline, la
     duración de ventana, la frecuencia, la métrica y el sha256 de los pesos
     del encoder con que se entrenó.
"""

import argparse
import glob
import json
import os
import pickle
import sys
from datetime import datetime, timezone

import numpy as np

import encoder

WINDOW_SEC = 30
BVP_HZ = 64
LABEL_HZ = 700
PURITY = 0.9
CLASS_OF = {1: 0, 2: 1, 3: 0}   # WESAD → sin estrés (0) / estrés (1)


def log(msg):
    print(f"[train] {msg}", flush=True)


def subject_files(root):
    files = sorted(glob.glob(os.path.join(root, "S*", "S*.pkl")))
    if not files:
        files = sorted(glob.glob(os.path.join(root, "*", "S*", "S*.pkl")))
    if not files:
        sys.exit(f"[train] no hay S*/S*.pkl en {root}")
    return files


def subject_windows(path):
    """(ventanas (n, 1, L), clases (n,)) de un sujeto."""
    with open(path, "rb") as f:
        data = pickle.load(f, encoding="latin1")
    bvp = np.asarray(data["signal"]["wrist"]["BVP"], dtype=np.float64).ravel()
    labels = np.asarray(data["label"]).ravel()
    del data

    t = np.arange(len(bvp)) / BVP_HZ
    grid, x = encoder.preprocess(t, bvp)
    starts, batch = encoder.windows(grid, x, WINDOW_SEC)

    keep, classes = [], []
    for i, s in enumerate(starts):
        seg = labels[int(round(s * LABEL_HZ)) : int(round((s + WINDOW_SEC) * LABEL_HZ))]
        if len(seg) == 0:
            continue
        values, counts = np.unique(seg, return_counts=True)
        top = values[np.argmax(counts)]
        if top in CLASS_OF and counts.max() >= PURITY * len(seg):
            keep.append(i)
            classes.append(CLASS_OF[int(top)])
    return batch[keep], np.array(classes, dtype=int)


def main():
    parser = argparse.ArgumentParser(description="Entrena el clasificador de estrés sobre WESAD")
    parser.add_argument("wesad_dir", help="carpeta con S2/S2.pkl … S17/S17.pkl")
    parser.add_argument("--out", default="/app/artefactos/clasificador.joblib")
    args = parser.parse_args()

    import joblib
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, f1_score
    from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    net, weights_path, digest = encoder.load_encoder()
    log(f"encoder {weights_path} (sha256 {digest[:12]})")

    X, y, groups = [], [], []
    for path in subject_files(args.wesad_dir):
        subject = os.path.basename(os.path.dirname(path))
        batch, classes = subject_windows(path)
        if len(classes) == 0:
            log(f"{subject}: sin ventanas válidas, se omite")
            continue
        X.append(encoder.embed(net, batch))
        y.append(classes)
        groups += [subject] * len(classes)
        log(f"{subject}: {len(classes)} ventanas, {int(classes.sum())} de estrés")
    X, y, groups = np.concatenate(X), np.concatenate(y), np.array(groups)
    n_subjects = len(set(groups))
    log(f"total: {len(y)} ventanas de {n_subjects} sujetos, {int(y.sum())} de estrés")

    pipeline = make_pipeline(
        StandardScaler(),
        LogisticRegression(class_weight="balanced", max_iter=10_000, random_state=42),
    )
    cv = GroupKFold(n_splits=min(5, n_subjects))
    search = GridSearchCV(
        pipeline,
        {"logisticregression__C": [0.001, 0.01, 0.1, 1, 10]},
        cv=cv, scoring="f1_macro", n_jobs=-1,
    )
    search.fit(X, y, groups=groups)
    best_c = search.best_params_["logisticregression__C"]

    # Métrica honesta del C elegido: predicción de cada ventana con un modelo
    # que no vio a ese sujeto.
    pred = cross_val_predict(search.best_estimator_, X, y, groups=groups, cv=cv)
    f1 = round(float(f1_score(y, pred, average="macro")), 3)
    bacc = round(float(balanced_accuracy_score(y, pred)), 3)
    log(f"C={best_c}: F1 macro {f1}, exactitud balanceada {bacc} (sujetos fuera)")

    final = search.best_estimator_.fit(X, y)
    artifact = {
        "pipeline": final,
        "window_sec": WINDOW_SEC,
        "fs": encoder.FS,
        "labels": {0: "sin_estres", 1: "estres"},
        "cv_f1_macro": f1,
        "cv_balanced_accuracy": bacc,
        "C": best_c,
        "windows": int(len(y)),
        "subjects": n_subjects,
        "trained_on": "WESAD (BVP muñeca), estrés vs base+diversión",
        "weights_sha256": digest,
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    joblib.dump(artifact, args.out)
    meta = {k: v for k, v in artifact.items() if k != "pipeline"}
    with open(os.path.splitext(args.out)[0] + ".json", "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    log(f"guardado {args.out}")


if __name__ == "__main__":
    main()

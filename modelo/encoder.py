"""Piezas compartidas por infer.py y train.py: pesos, preprocesado y embeddings.

Entrenamiento e inferencia usan exactamente las mismas funciones, así que el
clasificador ve en producción la misma señal que vio al entrenarse.

Preprocesado (el de los datasets de evaluación de Pulse-PPG, simplificado):
  1. Remuestrear a 50 Hz sobre una rejilla uniforme (interpolación lineal por
     el tiempo real de cada muestra, así el jitter de la Pi no importa).
  2. Pasabanda Butterworth 0.5–10 Hz de orden 2, sin desfase (filtfilt).
  3. Cortar en ventanas contiguas de window_sec y normalizar z cada ventana.
     (La red además aplica InstanceNorm a la entrada.)
"""

import glob
import hashlib
import os
import warnings

import numpy as np
from scipy.signal import butter, filtfilt

FS = 50                   # Hz: frecuencia de entrada de Pulse-PPG
BAND_HZ = (0.5, 10.0)
WEIGHTS_DIR = os.environ.get("PULSEPPG_WEIGHTS_DIR", "/opt/pulseppg")


def find_weights(root=WEIGHTS_DIR):
    """Ruta del checkpoint del encoder Pulse-PPG dentro de los pesos de Zenodo.

    El zip trae también el checkpoint de MotifDist (la red auxiliar del
    preentrenamiento), que no sirve aquí. PULSEPPG_WEIGHTS apunta a un archivo
    concreto si la estructura cambia.
    """
    explicit = os.environ.get("PULSEPPG_WEIGHTS")
    if explicit:
        return explicit
    found = sorted(glob.glob(os.path.join(root, "**", "*.pkl"), recursive=True))
    candidates = [p for p in found if "motifdist" not in p.lower()]
    best = [p for p in candidates if os.path.basename(p) == "checkpoint_best.pkl"]
    if len(best) == 1:
        return best[0]
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(
        f"no se encontró un único checkpoint de Pulse-PPG en {root} "
        f"(candidatos: {candidates or found or 'ninguno'}); "
        "definir PULSEPPG_WEIGHTS con la ruta"
    )


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_encoder(path=None):
    """Devuelve (red en modo eval, ruta, sha256 del checkpoint)."""
    import torch

    from resnet1d import PULSEPPG_PARAMS, Net

    path = path or find_weights()
    # weights_only=False: el checkpoint guarda también el estado del optimizador
    # y la época. Es un archivo fijo, descargado en el build desde Zenodo.
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    state = ckpt
    if isinstance(ckpt, dict):
        for key in ("net", "trained_net", "state_dict"):
            if key in ckpt:
                state = ckpt[key]
                break
    state = {k.removeprefix("module."): v for k, v in state.items()}

    # InstanceNorm1d del original se crea con num_features del último bloque;
    # sin affine no se usa y torch solo avisa. Se silencia ese aviso.
    warnings.filterwarnings("ignore", message="input's size at dim=1 does not match")
    net = Net(**PULSEPPG_PARAMS)
    net.load_state_dict(state, strict=True)
    net.eval()
    torch.set_grad_enabled(False)
    return net, path, sha256_of(path)


def preprocess(t, x, fs=FS):
    """(t en s, señal cruda) → (rejilla t uniforme a fs, señal filtrada)."""
    t = np.asarray(t, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    order = np.argsort(t, kind="stable")
    t, x = t[order], x[order]
    grid = np.arange(t[0], t[-1] + 1e-9, 1.0 / fs)
    uniform = np.interp(grid, t, x)
    b, a = butter(2, [BAND_HZ[0] / (fs / 2), BAND_HZ[1] / (fs / 2)], btype="band")
    # filtfilt necesita más de 3·max(len(a), len(b)) muestras.
    if len(uniform) <= 3 * max(len(a), len(b)):
        raise ValueError(f"señal demasiado corta para filtrar ({len(uniform)} muestras)")
    return grid, filtfilt(b, a, uniform)


def windows(grid, x, window_sec, fs=FS):
    """Ventanas contiguas completas: (t_inicio[n], arreglo (n, 1, window_sec·fs)).

    La cola que no llega a una ventana entera se descarta.
    """
    size = int(round(window_sec * fs))
    n = len(x) // size
    if n == 0:
        return np.empty(0), np.empty((0, 1, size), dtype=np.float32)
    w = x[: n * size].reshape(n, size)
    std = w.std(axis=1, keepdims=True)
    w = (w - w.mean(axis=1, keepdims=True)) / np.where(std > 0, std, 1.0)
    starts = grid[: n * size : size]
    return starts, w[:, None, :].astype(np.float32)


def embed(net, batch, chunk=32):
    """(n, 1, largo) float32 → embeddings (n, 512) float32."""
    import torch

    out = [net(torch.from_numpy(batch[i : i + chunk])).numpy()
           for i in range(0, len(batch), chunk)]
    if not out:
        return np.empty((0, net.out_dim), dtype=np.float32)
    return np.concatenate(out).astype(np.float32)

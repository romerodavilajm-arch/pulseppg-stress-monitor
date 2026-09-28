FROM python:3.12-slim

# PyTorch solo CPU (≈200 MB en lugar de los ≈3 GB de la versión con CUDA).
# La inferencia de una sesión son ~10 ventanas: no hace falta GPU.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX_URL} torch==2.8.0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pesos del encoder Pulse-PPG publicados por sus autores en Zenodo
# (github.com/maxxu05/pulseppg, download_model.sh). Se bajan y descomprimen en
# el mismo paso para no dejar el zip en la imagen. Con la URL vacía no se
# descarga nada y hay que montar los pesos en /opt/pulseppg.
ARG PULSEPPG_WEIGHTS_URL=https://zenodo.org/records/17345536/files/pulseppg_model_weights.zip?download=1
RUN mkdir -p /opt/pulseppg && if [ -n "${PULSEPPG_WEIGHTS_URL}" ]; then \
      python -c "import sys, urllib.request, zipfile; \
urllib.request.urlretrieve(sys.argv[1], '/tmp/w.zip'); \
zipfile.ZipFile('/tmp/w.zip').extractall('/opt/pulseppg')" "${PULSEPPG_WEIGHTS_URL}" \
      && rm /tmp/w.zip; \
    fi

COPY resnet1d.py encoder.py infer.py train.py ./
# Clasificador entrenado con train.py (vacío hasta entrenarlo; ver TESTING.md).
COPY artefactos/ ./artefactos/

ENV PYTHONUNBUFFERED=1
# Comprobación rápida que corre "docker compose up". El watcher lanza el paso
# real con "python3 /app/infer.py <archivo> <session_pk>".
CMD ["python3", "/app/infer.py", "--check"]

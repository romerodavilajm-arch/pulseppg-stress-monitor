FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# writer.py y transfer.py vienen de pi/ (contexto adicional "pi" en compose.yaml):
# el simulador ejecuta el mismo código de escritura y subida que la Pi real.
COPY --from=pi writer.py transfer.py ./
COPY simulador.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "simulador.py"]

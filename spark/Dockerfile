# Imagen oficial de Apache Spark: trae Java 17, Spark 4.0 y Python 3.
FROM spark:4.0.1-scala2.13-java17-python3-ubuntu

USER root
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt
# pyspark ya viene en la imagen; así "python3 hrv.py" lo encuentra sin spark-submit.
ENV PYTHONPATH=/opt/spark/python/lib/pyspark.zip:/opt/spark/python/lib/py4j-0.10.9.9-src.zip \
    PYTHONUNBUFFERED=1
COPY hrv.py /app/hrv.py

USER spark
WORKDIR /app
# Sin el entrypoint de la imagen (arranca un nodo del clúster): aquí es un job local.
ENTRYPOINT []
# Comando por defecto: comprobación rápida que corre "docker compose up". El
# watcher lanza el job real con "python3 /app/hrv.py <archivo> <session_pk>".
CMD ["python3", "-c", "import pyspark, psycopg; print('[spark] imagen lista, pyspark', pyspark.__version__)"]

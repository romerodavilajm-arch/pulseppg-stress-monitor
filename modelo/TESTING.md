# Pruebas de la Fase 6: modelo de estrés (Pulse-PPG)

Guía manual para verificar que, después de Spark, el watcher lanza un
contenedor efímero `modelo` que corta la sesión en ventanas de 30 s, calcula
el embedding de cada una con el encoder Pulse-PPG, las clasifica en `estres` /
`sin_estres` y escribe `stress_windows`. La sesión solo llega a `ready` si
Spark y el modelo terminan bien.

## Cómo está armado

- **Encoder**: Pulse-PPG (Saha, Xu et al., IMWUT 2025,
  github.com/maxxu05/pulseppg, licencia MIT). ResNet1D de un canal a 50 Hz,
  28.5 M de parámetros, devuelve 512 valores por ventana. Sus pesos se
  descargan de Zenodo durante el `docker compose build` y **no se reentrenan**.
- **Clasificador**: Pulse-PPG no da etiquetas de estrés. Encima va un
  StandardScaler + regresión logística, entrenado una vez sobre WESAD con
  `train.py` (misma idea que la evaluación "WESAD | Stress (2) | Linear
  Probe" del paper). Se guarda en `modelo/artefactos/clasificador.joblib` y se
  versiona en el repositorio.
- **Salida** por ventana: `level` = `estres` si la probabilidad ≥ 0.5, si no
  `sin_estres`; `score` = probabilidad de estrés (0–1).

Con el simulador las etiquetas no significan nada (la señal es sintética):
solo prueban que el flujo funciona. Tampoco hay validación con el MAX30102:
el encoder se entrenó con PPG de muñeca y el sensor va en el dedo.

## 1. Entrenar el clasificador (una sola vez)

Hasta que exista `modelo/artefactos/clasificador.joblib`, `compose up`
funciona y Spark sigue llenando `metrics`, pero cada sesión termina en
`error` con el motivo "falta el clasificador" (sección 5).

1. Descargar WESAD (≈2.5 GB; licencia solo para uso académico no comercial)
   desde la página de sus autores:
   https://ubicomp.eti.uni-siegen.de/home/datasets/icmi18/ y descomprimirlo.
   Debe quedar una carpeta con `S2/S2.pkl` … `S17/S17.pkl`.
2. Construir la imagen (baja PyTorch CPU y los pesos de Zenodo):
   ```bash
   docker compose build modelo
   ```
3. Entrenar, montando WESAD en solo lectura y `modelo/artefactos` para que
   el resultado quede en el repositorio:
   ```bash
   docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
     -v "$PWD/modelo/artefactos:/app/artefactos" \
     -v /ruta/a/WESAD:/data/wesad:ro \
     modelo python3 /app/train.py /data/wesad
   ```
   Corre en CPU (15 sujetos, unos 100 min de señal cada uno). La salida
   tiene esta forma:
   ```
   [train] encoder /opt/pulseppg/.../checkpoint_best.pkl (sha256 ...)
   [train] S2: <n> ventanas, <m> de estrés
   ...
   [train] C=<c>: F1 macro <f1>, exactitud balanceada <ba> (sujetos fuera)
   [train] guardado /app/artefactos/clasificador.joblib
   ```
   La métrica es sobre sujetos que el clasificador no vio (GroupKFold): es la
   cifra honesta para una persona nueva. Queda también en
   `clasificador.json`.
4. Reconstruir la imagen para que incluya el clasificador y versionarlo:
   ```bash
   docker compose build modelo
   git add modelo/artefactos/clasificador.joblib modelo/artefactos/clasificador.json
   ```

## 2. Levantar todo

```bash
SIM_DURATION_SEC=70 docker compose up -d --build
docker compose ps -a
```

- `spark` y `modelo`: `Exited (0)`. En `compose up` solo comprueban sus
  imágenes; el watcher no arranca si alguna falla.
  ```bash
  docker compose logs modelo
  ```
  ```
  [modelo] imagen lista, encoder Pulse-PPG pulseppg/experiments/out/pulseppg/checkpoint_best.pkl (sha256 ..., salida 512)
  [modelo] clasificador: ventanas de 30 s, F1 macro por sujeto <f1>
  ```
  Sin clasificador, la segunda línea es un aviso:
  `[modelo] aviso: falta /app/artefactos/clasificador.joblib; entrenar con train.py`.

## 3. Una sesión llega a `ready` con sus ventanas

1. Abrir `http://localhost:5000` y presionar "Comenzar prueba". Con
   `SIM_DURATION_SEC=70` salen 2 ventanas de 30 s (la cola de 10 s se
   descarta); con los 300 s por defecto, 10.
2. Logs del watcher (el modelo tarda unos 4 s, casi todo en cargar PyTorch):
   ```bash
   docker compose logs -f watcher
   ```
   ```
   watcher.runner 20260928T095551Z_sim01: 'processing' (pk=2)
   watcher.runner   [spark] {"samples": 3500, "duration_sec": 69.98, ... "bpm": 72.01, ...}
   watcher.runner spark terminó en 10.5 s (intento 1)
   watcher.runner   [modelo] {"samples": 3500, "duration_sec": 69.98, "windows": 2, "window_sec": 30, "estres": ..., "score_mean": ...}
   watcher.runner modelo terminó en 4.2 s (intento 1)
   watcher.runner 20260928T095551Z_sim01: 'ready'
   ```
3. En PostgreSQL:
   ```bash
   docker compose exec postgres psql -U pulseppg -d pulseppg \
     -c "SELECT id, session_id, status, status_detail FROM sessions" \
     -c "SELECT session_pk, t_start, t_end, level, round(score::numeric, 3) AS score
         FROM stress_windows ORDER BY session_pk, t_start"
   ```
   Una fila por ventana, con `t_start` 0, 30, 60… y `t_end = t_start + 30`.
4. No queda ningún contenedor colgado:
   ```bash
   docker ps -a --filter label=pulseppg.step
   ```

## 4. El modelo por separado

Sobre cualquier archivo que ya esté en `/data/raw/`:

```bash
# Solo imprime, una línea por ventana
docker compose run --rm modelo python3 /app/infer.py /data/raw/<session_id>.jsonl --no-db
# Escribe para la sesión <pk> (sessions.id)
docker compose run --rm modelo python3 /app/infer.py /data/raw/<session_id>.jsonl <pk>
```

Correrlo dos veces sobre la misma sesión deja las mismas filas en
`stress_windows`: reemplaza, no acumula.

Códigos de salida (`echo $?`): `0` OK, `2` entrada inválida (sin muestras,
menos de 30 s de señal, sesión inexistente) o falta el clasificador, `1`
error inesperado.

## 5. Sin clasificador: reintento y `error`

Con la imagen construida antes de entrenar (sección 1), cualquier sesión
termina así, sin tocar `metrics` ni `peaks` que ya escribió Spark:

```
watcher.runner <id>: modelo falló (código 2: [modelo] falta el clasificador /app/artefactos/clasificador.joblib: entrenarlo una vez con train.py (ver modelo/TESTING.md, sección 1)), reintento en 3 s
watcher.runner <id>: modelo falló dos veces, sesión en 'error'; se conserva /data/raw/<id>.jsonl
```

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "SELECT session_id, status, status_detail FROM sessions"
```

## Qué hay en la imagen

| Ruta | Contenido |
|------|-----------|
| `/opt/pulseppg/` | Pesos de Zenodo tal cual los publica `download_model.sh` del repo de Pulse-PPG |
| `/app/resnet1d.py` | Red del encoder copiada del repo original (MIT), sin cambios de arquitectura |
| `/app/encoder.py` | Carga de pesos, preprocesado (50 Hz, 0.5–10 Hz, ventanas) y embeddings; lo comparten `infer.py` y `train.py` |
| `/app/artefactos/` | Clasificador entrenado |

Si Zenodo cambia la estructura del zip y no se encuentra un único
checkpoint, el mensaje lista los candidatos; se elige uno con
`PULSEPPG_WEIGHTS=/opt/pulseppg/<ruta>`.

# Prueba del sistema completo (Fase 11)

Un solo `docker compose up` construye y levanta todo, y una prueba automática
recorre el flujo de punta a punta como lo haría el navegador. Se usa el
simulador como Pi.

Requisito en la máquina: Docker con Compose v2 (`docker compose version`).
Nada más: Python, Spark y PyTorch viven en las imágenes.

## 1. Levantar desde cero

```bash
git clone https://github.com/romerodavilajm-arch/pulseppg-stress-monitor
cd pulseppg-stress-monitor
SIM_DURATION_SEC=60 docker compose up -d --build --wait
```

La primera vez tarda: baja las imágenes base (Postgres, Python, Spark) y en
el build del modelo instala PyTorch CPU y descarga los pesos de Pulse-PPG de
Zenodo. `--wait` vuelve cuando todo está listo:

```bash
docker compose ps -a
```

| Servicio | Estado esperado |
|----------|-----------------|
| `postgres`, `servidor-web`, `watcher` | `Up (healthy)` |
| `simulador-pi` | `Up` |
| `spark`, `modelo` | `Exited (0)`: solo comprueban su imagen; el watcher los lanza por sesión |

`SIM_DURATION_SEC=60` acorta la captura del simulador (por defecto 300 s, como
la Pi real). No bajar de 30: el modelo clasifica ventanas de 30 s y con menos
la sesión queda en `error`. Para no repetirlo en cada comando, copiar
`.env.example` como `.env` y ajustarlo ahí.

## 2. Prueba de punta a punta

```bash
docker compose --profile e2e run --rm e2e
```

Hace de navegador (Socket.IO y la API HTTP) y comprueba, sin intervención
manual:

1. **Servicios**: servidor-web y watcher sanos, la Pi registrada, la página
   responde.
2. **Sesión completa**: `start` → captura → `done` → spark y modelo → sesión
   en `ready` con métricas, picos y ventanas de estrés; historial
   `uploaded → processing → ready`; el crudo ya no está en `/data/raw/`; la
   sesión aparece en la lista de la bienvenida.
3. **Cancelación**: `start` y `abort` a los ~2 s → sesión en `error` con
   `abort: ...`, historial `created → error`, sin métricas.

Salida esperada (con `SIM_DURATION_SEC=60`, ~2 min; las cifras cambian de una
corrida a otra):

```
1. Servicios
  ✓ servidor-web y watcher sanos (el watcher llega a PostgreSQL)
  ✓ Pi registrada en el servidor-web
  ✓ la página responde en /
  ✓ GET /api/sesiones responde
2. Sesión completa
  ✓ captura iniciada: 20260929T175750Z_sim01
    esperando 60 s de captura...
  ✓ JSONL subido al watcher con SHA-256 (status done)
  ✓ el servidor avisa 'analyzing' al navegador
    estado: processing (spark)
    estado: ready (análisis OK)
  ✓ sesión en 'ready' en 16 s
  ✓ métricas de Spark: BPM 71.9671, SDNN 32.22077 ms, RMSSD 40.0 ms, pNN50 21.73913
  ✓ 71 picos guardados
  ✓ modelo: 1 ventanas de 30 s, 0 con estrés, nivel global bajo
  ✓ historial de estados: uploaded → processing → ready
  ✓ crudo borrado de /data/raw tras 'ready'
  ✓ aparece en las últimas sesiones de la bienvenida
3. Cancelación
  ✓ captura iniciada: 20260929T175906Z_sim01
  ✓ la Pi confirma 'aborted' y el navegador vuelve a bienvenida
    estado: error (abort: cancelada por el usuario a los 2 s)
  ✓ sesión en 'error' como cancelada: abort: cancelada por el usuario a los 2 s
  ✓ historial de estados: created → error
  ✓ sin métricas ni ventanas
OK: el sistema completo funciona de punta a punta.
```

Termina con código 0 si todo pasó y 1 si algo falló (el `✗` dice qué). Las
ventanas y el nivel de estrés del simulador no significan nada: la señal es
sintética.

`SKIP_ABORT=1` salta la cancelación:
`docker compose --profile e2e run --rm -e SKIP_ABORT=1 e2e`.

## 3. La demo en el navegador

Abrir `http://localhost:5000` y seguir el guion de la sección 14 de la
[propuesta](../docs/Propuesta%20técnica.md): "Comenzar prueba", onda en vivo
con contador, "Analizando..." y resultados. Cómo revisar cada pantalla:
[servidor-web/TESTING.md](../servidor-web/TESTING.md).

## 4. Los datos sobreviven a reinicios

```bash
docker compose down
docker compose up -d --wait
curl -s localhost:5000/api/sesiones
```

Las sesiones siguen ahí (volumen `pgdata`). `docker compose down -v` borra
también los volúmenes y deja la máquina como al principio.

## 5. Con la Pi real

Sin el simulador (si no, las dos "Pi" se disputan el servidor-web):

```bash
docker compose up -d --build --wait postgres servidor-web watcher
```

Eso también construye y comprueba spark y modelo (el watcher depende de
ellos). La Pi se conecta a `http://<ip-de-la-PC>:5000` y sube a
`http://<ip-de-la-PC>:5001`. La prueba del paso 2 funciona igual con la Pi
real si alguien pone el dedo en el sensor.

# Estado actual del proyecto

## Fase
Fase 6 — Modelo de clasificación ✅ Código completo y probado con pesos de prueba; falta entrenar el clasificador con WESAD

## Completado
- Repositorio en GitHub creado con estructura inicial
- Proyecto en Claude configurado con contexto
- Esquema de PostgreSQL con 5 tablas: sessions, metrics, peaks, stress_windows, session_events
- Máquina de estados aplicada en la base (adelanta la Fase 7): el trigger `trg_sessions_enforce_state` solo permite crear sesiones en `created` o `uploaded`, rechaza transiciones inválidas (created → uploaded → processing → ready, con salida a error desde created o processing), acepta repetir el estado actual como no-op y refresca `updated_at` en cada UPDATE
- Historial de estados: el trigger `trg_sessions_log_event` registra en session_events la creación y cada cambio real de estado, con su motivo (`status_detail`)
- ON DELETE CASCADE verificado
- Constraint único de session_id verificado
- compose.yaml mínimo con PostgreSQL
- Documentos de verificación db/TESTING.md
- servidor-web mínimo (Flask + Flask-SocketIO, :5000): reenvía `sample`, `countdown` y `status` de la Pi al navegador, y `start`/`abort` del navegador a la Pi. Aún no escribe en PostgreSQL
- Página mínima que dibuja la onda IR en vivo (canvas, sin Chart.js todavía) con botones Comenzar/Cancelar
- Simulador de la Pi (`simulador/`) que emite una señal PPG sintética a 50 Hz; en compose como `simulador-pi`
- Guía de verificación servidor-web/TESTING.md
- watcher (Flask, :5001) con `POST /upload`: verifica SHA-256 y formato del JSONL, idempotencia por `session_id` (200 en reintentos, 409 si la sesión ya avanzó), deja el archivo en `/data/raw/` (volumen `rawdata`) y la sesión en `uploaded`. Escribe primero en `/data/raw/.incoming/` para que el watchdog de la Fase 5 nunca vea archivos a medias
- `pi/writer.py` (JSONL) y `pi/transfer.py` (SHA-256, 3 intentos con 0/2/5 s, `completed/` y `pending/`): código de la Pi que no depende del sensor; el simulador lo usa tal cual
- El simulador escribe el JSONL, lo sube al terminar y emite `status: uploading` y luego `done` (o `upload_failed`); al abortar borra el JSONL
- Guía de verificación watcher/TESTING.md
- Watchdog en el watcher (`watcher/watch.py`): detecta cada `<session_id>.jsonl` que llega a `/data/raw/` (ignora `.incoming/` y ocultos)
- Lanzador de análisis (`watcher/runner.py`): pasa la sesión a `processing`, lanza un contenedor efímero de Spark con el volumen `rawdata` en solo lectura, reintenta una vez tras 3 s y deja la sesión en `ready` o en `error` con el motivo (el crudo se conserva). Al arrancar retoma las sesiones en `uploaded` o `processing`
- Spark (`spark/hrv.py`, imagen oficial Spark 4.0.1): lee el JSONL con `spark.read.json()`, detecta picos en IR con funciones de ventana, calcula BPM, SDNN, RMSSD y pNN50 y escribe `peaks` y `metrics` de forma idempotente. Con la señal simulada da 72 BPM y SDNN ≈ 32 ms
- Guía de verificación spark/TESTING.md
- Modelo (`modelo/`, contenedor efímero): encoder Pulse-PPG (pesos de Zenodo bajados en el build, PyTorch solo CPU) + clasificador lineal propio. Corta la sesión en ventanas de 30 s, clasifica cada una en `estres` / `sin_estres` con su probabilidad y reemplaza las filas de `stress_windows`. El watcher lo lanza después de Spark (segundo paso de `ANALYSIS_STEPS`), con el mismo reintento; `status_detail` indica el paso en curso
- `modelo/train.py`: entrenamiento único del clasificador sobre WESAD (BVP de muñeca, estrés vs base+diversión, validación dejando sujetos fuera). El resultado va en `modelo/artefactos/clasificador.joblib` y se versiona
- Guía de verificación modelo/TESTING.md

## En progreso
- Fases 2 y 4 — probar con la Pi real y el MAX30102 (a la espera del sensor)

## Pendiente
- Fase 3 — Captura completa en la Pi: falta la lectura del MAX30102 y la validación de calidad; la escritura del JSONL ya está en `pi/writer.py`
- Fase 4 — ✅ con simulador (ver Completado)
- Fase 5 — ✅ con simulador (ver Completado)
- Fase 6 — ✅ código (ver Completado); falta entrenar y versionar el clasificador (modelo/TESTING.md, sección 1)
- Fase 7 — Máquina de estados ✅ ya aplicada en la base (ver Completado)
- Fase 8 — Pantalla única con 4 estados
- Fase 9 — Cancelación con abort
- Fase 10 — Limpieza de sesiones viejas
- Fase 11 — Compose completo

## Bloqueos
- Sin sensor MAX30102 todavía: el código de la Pi que lee el sensor espera; se avanza con el simulador.
- Clasificador de estrés sin entrenar: requiere descargar WESAD (≈2.5 GB) y correr `train.py` una vez. Mientras tanto cada sesión termina en `error` con el motivo "falta el clasificador" (Spark sí escribe `metrics` y `peaks`).

## Próximos pasos
Entrenar el clasificador con WESAD y versionarlo. Luego, Fase 8: pantalla única que muestre métricas y la línea de tiempo de `stress_windows`.

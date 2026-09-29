# Estado actual del proyecto

## Fase
Fase 11 — Compose completo ✅ Probada de punta a punta con el simulador

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
- Clasificador entrenado con WESAD y versionado (F1 macro 0.822, exactitud balanceada 0.844 dejando sujetos fuera)
- Pantalla única (Fase 8): bienvenida con instrucciones y últimas sesiones, captura en vivo (onda, contador, pulso estimado, calidad), "Analizando..." con polling de `/api/estado` cada 2 s, y resultados (BPM, SDNN, RMSSD, estado global, línea de tiempo de `stress_windows` y detalle técnico con ventanas e historial de estados). "Nueva sesión" vuelve a bienvenida. Una sesión en `error` muestra el motivo
- servidor-web: `GET /api/estado/<session_id>`, `GET /api/resultados/<session_id>` y `GET /api/sesiones` (solo lectura de PostgreSQL)
- La Pi (y el simulador) incluye el `session_id` en cada `status`
- Cancelación (Fase 9): al abortar, la Pi borra el JSONL, avisa al watcher con `POST /abort` y emite `aborted`. El watcher deja la sesión en `error` con la duración parcial y `status_detail` "abort: ..." (historial `created` → `error`). Si el watcher no responde, el aviso queda en `pending/` y se reintenta. La lista de bienvenida la muestra como "cancelada" y su detalle dice "Prueba cancelada"
- Limpieza (Fase 10, `watcher/cleanup.py`): al quedar una sesión en `ready` el watcher borra `/data/raw/<session_id>.jsonl` (en `error` lo conserva). Tras cada sesión que termina (lista, con error o cancelada) conserva solo las 10 más recientes por `start_time`: borra las más viejas que estén en `ready` o `error` (con su crudo, si quedaba) y ON DELETE CASCADE se lleva métricas, picos, ventanas e historial. Al arrancar barre los `.part` de `.incoming/`, el crudo de sesiones ya listas y los archivos sin sesión en la base
- Compose completo (Fase 11): `docker compose up -d --build --wait` construye y levanta todo en una máquina con solo Docker (3 permanentes sanos, spark y modelo comprueban su imagen y terminan, simulador conectado). Variables opcionales en `.env.example` (credenciales, `SIM_DURATION_SEC`, `MAX_SESSIONS`)
- Prueba automática de punta a punta (`e2e/prueba.py`, perfil `e2e`): `docker compose --profile e2e run --rm e2e` hace de navegador, corre una sesión completa (hasta `ready` con métricas, ventanas, historial y crudo borrado) y una cancelada (`error` con "abort: ..."), y sale con 0 o 1. Guía en e2e/TESTING.md

## En progreso
- Fases 2 y 4 — probar con la Pi real y el MAX30102 (a la espera del sensor)

## Pendiente
- Fase 3 — Captura completa en la Pi: falta la lectura del MAX30102 y la validación de calidad; la escritura del JSONL ya está en `pi/writer.py`
- Fase 4 — ✅ con simulador (ver Completado)
- Fase 5 — ✅ con simulador (ver Completado)
- Fase 6 — ✅ (ver Completado)
- Fase 7 — Máquina de estados ✅ ya aplicada en la base (ver Completado)
- Fase 8 — ✅ con simulador (ver Completado)
- Fase 9 — ✅ con simulador (ver Completado)
- Fase 10 — ✅ con simulador (ver Completado)
- Fase 11 — ✅ con simulador (ver Completado)

## Bloqueos
- Sin sensor MAX30102 todavía: el código de la Pi que lee el sensor espera; se avanza con el simulador.

## Próximos pasos
Las 11 fases de la hoja de ruta están hechas con el simulador. Falta la Fase 3 con hardware: leer el MAX30102 y validar la calidad en la Pi 5, y repetir la prueba de punta a punta con la Pi real (e2e/TESTING.md, sección 5).

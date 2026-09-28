# Estado actual del proyecto

## Fase
Fase 4 — POST con checksum al watcher ✅ Completada con simulador (falta probar con la Pi real)

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

## En progreso
- Fases 2 y 4 — probar con la Pi real y el MAX30102 (a la espera del sensor)

## Pendiente
- Fase 3 — Captura completa en la Pi: falta la lectura del MAX30102 y la validación de calidad; la escritura del JSONL ya está en `pi/writer.py`
- Fase 4 — ✅ con simulador (ver Completado)
- Fase 5 — Watchdog + lanzamiento de Spark
- Fase 6 — Modelo de clasificación
- Fase 7 — Máquina de estados ✅ ya aplicada en la base (ver Completado)
- Fase 8 — Pantalla única con 4 estados
- Fase 9 — Cancelación con abort
- Fase 10 — Limpieza de sesiones viejas
- Fase 11 — Compose completo

## Bloqueos
- Sin sensor MAX30102 todavía: el código de la Pi que lee el sensor espera; se avanza con el simulador.

## Próximos pasos
Fase 5: watchdog sobre `/data/raw/` en el watcher y lanzamiento de Spark (`uploaded` → `processing`).

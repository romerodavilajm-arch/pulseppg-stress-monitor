# Estado actual del proyecto

## Fase
Fase 1 — Esquema de base de datos ✅ Completada

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

## En progreso
- Fase 2 — WebSocket Pi ↔ servidor-web

## Pendiente
- Fase 3 — Captura completa en la Pi
- Fase 4 — POST con checksum al watcher
- Fase 5 — Watchdog + lanzamiento de Spark
- Fase 6 — Modelo de clasificación
- Fase 7 — Máquina de estados ✅ ya aplicada en la base (ver Completado)
- Fase 8 — Pantalla única con 4 estados
- Fase 9 — Cancelación con abort
- Fase 10 — Limpieza de sesiones viejas
- Fase 11 — Compose completo

## Bloqueos
Ninguno.

## Próximos pasos
Diseñar el WebSocket entre la Pi y el servidor-web para el dibujo en vivo.

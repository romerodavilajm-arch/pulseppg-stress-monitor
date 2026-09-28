# Pruebas del esquema de base de datos

Guía manual para verificar que el esquema de PostgreSQL está correcto y sigue
funcionando después de cambios. No hay script automático a propósito: el esquema
cambia poco y las pruebas manuales son más fáciles de depurar cuando algo falla.

## 1. Levantar PostgreSQL

```bash
docker compose up -d postgres
```

Esperar a que esté sano:

```bash
docker compose ps
```

El campo `STATUS` debe decir `healthy`. Si dice `starting`, esperar unos segundos.
Si dice `unhealthy` o `exited`, revisar los logs:

```bash
docker compose logs --tail 30 postgres
```

## 2. Entrar a psql

La forma más cómoda:

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg
```

Para salir: `\q`.

Comandos útiles dentro de psql:

| Comando | Qué hace |
|---------|----------|
| `\dt` | Lista las tablas |
| `\d sessions` | Describe la tabla `sessions` |
| `\di` | Lista los índices |
| `\df` | Lista las funciones |
| `\d+ sessions` | Descripción detallada con constraints |
| `\q` | Salir |

## 3. Verificar que las tablas existen

Dentro de psql:

```sql
\dt
```

Deben aparecer las 5 tablas: `sessions`, `metrics`, `peaks`, `stress_windows`,
`session_events`.

Alternativa en una línea desde fuera:

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg -c '\dt'
```

## 4. Verificar índices

```sql
SELECT tablename, indexname FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;
```

Deben estar:

| Tabla | Índice |
|-------|--------|
| sessions | `idx_sessions_start` |
| sessions | `idx_sessions_status` |
| sessions | `uq_sessions_session_id` |
| peaks | `uq_peaks_session_t` |
| stress_windows | `uq_windows_session_start` |
| session_events | `idx_events_session` |

## 5. Verificar ON DELETE CASCADE

```sql
SELECT conrelid::regclass AS tabla_hija
FROM pg_constraint
WHERE contype = 'f'
  AND confrelid = 'sessions'::regclass
  AND confdeltype = 'c';
```

Deben aparecer: `metrics`, `peaks`, `stress_windows`, `session_events`.

## 6. Probar el flujo de estados (caso feliz)

Abre una transacción para no dejar datos:

```sql
BEGIN;

INSERT INTO sessions (session_id, start_time) VALUES ('test_ok', now());
UPDATE sessions SET status = 'uploaded'   WHERE session_id = 'test_ok';
UPDATE sessions SET status = 'processing' WHERE session_id = 'test_ok';
UPDATE sessions SET status = 'ready'      WHERE session_id = 'test_ok';

-- ¿Se registraron los 4 eventos? (alta + 3 transiciones)
SELECT from_status, to_status, changed_at
FROM session_events e
JOIN sessions s ON s.id = e.session_pk
WHERE s.session_id = 'test_ok'
ORDER BY changed_at;

ROLLBACK;
```

Deben aparecer 4 filas: `NULL → created`, `created → uploaded`,
`uploaded → processing`, `processing → ready`.

## 7. Probar transición inválida

```sql
BEGIN;

INSERT INTO sessions (session_id, start_time) VALUES ('test_bad', now());
UPDATE sessions SET status = 'uploaded'   WHERE session_id = 'test_bad';
UPDATE sessions SET status = 'processing' WHERE session_id = 'test_bad';
UPDATE sessions SET status = 'ready'      WHERE session_id = 'test_bad';

-- Esto debe fallar con "transición inválida"
UPDATE sessions SET status = 'processing' WHERE session_id = 'test_bad';

ROLLBACK;
```

Si el último `UPDATE` no da error, la máquina de estados está mal implementada.

## 8. Probar duplicado de session_id

```sql
BEGIN;

INSERT INTO sessions (session_id, start_time) VALUES ('test_dup', now());

-- Esto debe fallar con "unique_violation"
INSERT INTO sessions (session_id, start_time) VALUES ('test_dup', now());

ROLLBACK;
```

## 9. Probar cascada al borrar

```sql
BEGIN;

INSERT INTO sessions (session_id, start_time) VALUES ('test_cascade', now());

INSERT INTO metrics (session_pk, bpm, sdnn, rmssd, pnn50)
SELECT id, 72, 48, 35, 12 FROM sessions WHERE session_id = 'test_cascade';

INSERT INTO peaks (session_pk, t, rr_interval)
SELECT id, 0.8, NULL FROM sessions WHERE session_id = 'test_cascade';

INSERT INTO stress_windows (session_pk, t_start, t_end, level, score)
SELECT id, 0, 30, 'bajo', 0.2 FROM sessions WHERE session_id = 'test_cascade';

-- Guardar el id antes de borrar
SELECT id FROM sessions WHERE session_id = 'test_cascade';
-- (anotar el número que devuelve, por ejemplo 42)

DELETE FROM sessions WHERE session_id = 'test_cascade';

-- Verificar que no quedaron huérfanos (usar el id anotado)
SELECT COUNT(*) FROM metrics        WHERE session_pk = 42;  -- debe ser 0
SELECT COUNT(*) FROM peaks          WHERE session_pk = 42;  -- debe ser 0
SELECT COUNT(*) FROM stress_windows WHERE session_pk = 42;  -- debe ser 0
SELECT COUNT(*) FROM session_events WHERE session_pk = 42;  -- debe ser 0

ROLLBACK;
```

## 10. Probar abort (created → error)

```sql
BEGIN;

INSERT INTO sessions (session_id, start_time) VALUES ('test_abort', now());
UPDATE sessions
   SET status = 'error', status_detail = 'abort', duration_sec = 42
 WHERE session_id = 'test_abort';

SELECT from_status, to_status, detail
FROM session_events e
JOIN sessions s ON s.id = e.session_pk
WHERE s.session_id = 'test_abort';

ROLLBACK;
```

Debe haber un evento con `to_status = 'error'` y `detail = 'abort'`.

## 11. Si cambias el esquema

Cuando modifiques `db/init.sql`:

1. **Cambios menores** (añadir columna, índice): editar `init.sql` con `IF NOT EXISTS`,
   y aplicarlo a mano:
   ```bash
   docker compose exec -T postgres psql -U pulseppg -d pulseppg < db/init.sql
   ```
   No hay que reiniciar el contenedor.

2. **Cambios mayores** (nueva tabla, cambio de constraint): lo más limpio en desarrollo
   es borrar el volumen y dejar que `init.sql` se ejecute de nuevo:
   ```bash
   docker compose down -v
   docker compose up -d postgres
   ```
   Esto pierde los datos existentes. En producción, no lo hagas; en desarrollo, es
   lo más rápido.

3. **Si el cambio es destructivo** (renombrar columna, cambiar tipo): añade el cambio
   a `init.sql` y crea un `db/migrations/002_descripcion.sql` con el `ALTER TABLE`.
   Documenta en `docs/Decisiones.md` por qué se hizo.

## 12. Si algo falla

### El contenedor no queda `healthy`

```bash
docker compose logs --tail 50 postgres
```

Buscar líneas con `ERROR` o `FATAL`. Causas comunes:

- `init.sql` tiene un error de sintaxis. Probar:
  ```bash
  docker compose down -v
  docker compose up -d postgres
  docker compose logs postgres | grep -i error
  ```
- El puerto está ocupado por otro PostgreSQL. Raro, porque no exponemos puertos.

### Las tablas no aparecen

`init.sql` solo se ejecuta la primera vez que arranca con el volumen vacío. Si el
volumen ya existía, no se ejecutó:

```bash
docker compose down -v
docker compose up -d postgres
```

### Las transiciones no se registran en `session_events`

Verificar que el trigger existe:

```sql
SELECT tgname, tgrelid::regclass
FROM pg_trigger
WHERE tgrelid = 'sessions'::regclass AND NOT tgisinternal;
```

Deben aparecer `trg_sessions_enforce_state` y `trg_sessions_log_event`.

### Cambié `init.sql` pero el esquema no cambia

Porque `init.sql` solo se ejecuta con el volumen vacío. Aplicarlo a mano (sección 11)
o borrar el volumen.

## 13. Apagar

```bash
docker compose down        # detiene, conserva datos
docker compose down -v     # detiene, borra datos
```

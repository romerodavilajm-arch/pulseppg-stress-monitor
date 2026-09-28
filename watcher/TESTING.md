# Pruebas del watcher: transferencia (Fase 4), abort (Fase 9) y limpieza (Fase 10)

Guía manual para verificar que, al terminar una sesión, el JSONL llega al
watcher con su SHA-256, aparece en `/data/raw/` y la sesión queda en
`uploaded` en PostgreSQL. Se usa el simulador como Pi.

Desde la Fase 5 cada archivo aceptado se analiza enseguida: donde esta guía
dice `uploaded`, unos 15 s después la sesión ya estará en `ready` (o en
`error` si el archivo no tiene latidos). El análisis se prueba en
[spark/TESTING.md](../spark/TESTING.md).

## 1. Levantar todo con sesiones cortas

```bash
SIM_DURATION_SEC=8 docker compose up -d --build
docker compose ps
```

`postgres`, `servidor-web` y `watcher` deben estar `healthy`; `simulador-pi`, `Up`.

```bash
curl -s localhost:5001/health
```

Debe devolver `{"ok":true}` (el watcher llega a PostgreSQL).

## 2. Una sesión completa llega a `/data/raw/`

1. Abrir `http://localhost:5000` y presionar "Comenzar prueba".
2. A los 8 s el Estado pasa a `uploading` y enseguida a `done`.
3. Logs:
   ```bash
   docker compose logs simulador-pi watcher
   ```
   Deben aparecer, con el mismo `session_id` (formato `20260928T071519Z_sim01`):
   ```
   [simulador] captura ..._sim01 terminada: 400 muestras en /tmp/ppg/..._sim01.jsonl
   [simulador] transfer: ..._sim01: subido en el intento 1 (sha256 1d0e35d22888…)
   watcher upload ..._sim01 OK (nueva): pk=1, 400 muestras, 13920 bytes, 8.0 s, calidad=simulada
   ```
4. El archivo está en el watcher y su SHA-256 coincide con el del log:
   ```bash
   docker compose exec watcher sh -c 'ls -l /data/raw; wc -l /data/raw/*.jsonl; sha256sum /data/raw/*.jsonl'
   ```
   400 líneas del tipo `{"t":0.02,"ir":110461,"red":80261}`.
5. La Pi (simulador) conserva su respaldo en `completed/`:
   ```bash
   docker compose exec simulador-pi find /tmp/ppg -type f
   ```
6. La sesión está en `uploaded` y el historial lo registra:
   ```bash
   docker compose exec postgres psql -U pulseppg -d pulseppg \
     -c "SELECT id, session_id, device_id, duration_sec, quality, status, left(checksum, 12) FROM sessions" \
     -c "SELECT session_pk, from_status, to_status, detail FROM session_events"
   ```

## 3. Cancelar no sube nada

Presionar "Comenzar prueba" y a los pocos segundos "Cancelar". El Estado pasa
a `aborted` y no aparece ningún archivo nuevo en `/data/raw/`; en el simulador
no queda el JSONL de esa sesión (`find /tmp/ppg -type f`). Desde la Fase 9 sí
queda una fila en `sessions`, en `error` y sin checksum (ver sección 6).

## 4. Watcher caído: `pending/` y reintento

```bash
docker compose stop watcher
```

1. Hacer una sesión desde el navegador. Tras 3 intentos (0 s, 2 s, 5 s) el
   Estado pasa a `upload_failed` y la página avisa que se reintentará.
2. El archivo queda en `pending/` con su metadata:
   ```bash
   docker compose exec simulador-pi find /tmp/ppg -type f
   ```
   ```
   /tmp/ppg/pending/..._sim01.jsonl
   /tmp/ppg/pending/..._sim01.json
   ```
3. Levantar el watcher y hacer otra sesión:
   ```bash
   docker compose start watcher
   ```
   Antes de capturar, el simulador sube lo pendiente
   (`reintentando desde pending/` → `subido en el intento 1`) y luego la
   sesión nueva. Ambas quedan en `/data/raw/` y en `uploaded`. También se
   reintenta al reiniciar el simulador (`docker compose restart simulador-pi`).

## 5. Casos del POST a mano

Con un JSONL cualquiera (por ejemplo, el de la sesión anterior):

```bash
docker compose cp simulador-pi:/tmp/ppg/completed/<session_id>.jsonl s.jsonl
SUM=$(sha256sum s.jsonl | cut -d' ' -f1)
up() { curl -s -w " [%{http_code}]\n" -F session_id=$1 -F checksum=$2 \
         -F duration=8 -F quality=ok -F file=@$3 localhost:5001/upload; }
```

| Prueba | Comando | Esperado |
|--------|---------|----------|
| Reintento (se perdió el 200) | `up <session_id> $SUM s.jsonl` | `200`, `"outcome":"reintento"`, sin fila nueva |
| Checksum que no coincide | `up prueba_mala 0000…0000 s.jsonl` (64 ceros) | `400 checksum no coincide`, nada en `/data/raw/` |
| Mismo id, otro archivo | `head -5 s.jsonl > s2.jsonl`; `up <session_id> $(sha256sum s2.jsonl \| cut -d' ' -f1) s2.jsonl` | `409` |
| `session_id` con `/` o `..` | `up ../x $SUM s.jsonl` | `400 session_id inválido` |
| Archivo que no es JSONL de muestras | `echo hola > b.jsonl`; `up prueba_b $(sha256sum b.jsonl \| cut -d' ' -f1) b.jsonl` | `400 línea 1 no es una muestra válida` |

Sesión ya registrada por servidor-web (`created`, aún no lo hace; se simula a mano):

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "INSERT INTO sessions (session_id, start_time) VALUES ('prueba_created', now())"
up prueba_created $SUM s.jsonl          # 200, "outcome":"created -> uploaded"
# El watcher la analiza (Fase 5); cuando ya avanzó, el mismo POST se rechaza:
up prueba_created $SUM s.jsonl          # 409 la sesión ya está en 'processing' (o 'ready')
```

`/data/raw/.incoming/` debe quedar vacío después de todas las pruebas: ahí solo
viven los archivos mientras se reciben.

## 6. Usar la Pi real

La Pi hace `POST http://<ip-pc>:5001/upload` con los mismos campos que el
simulador (`pi/transfer.py`, que el simulador ya usa tal cual). Para levantar
solo los servicios del PC:

```bash
docker compose up -d postgres servidor-web watcher
```

## 7. Limpiar

```bash
docker compose down        # conserva la base y /data/raw
docker compose down -v     # borra también los volúmenes pgdata y rawdata
```

## 6. Fase 9: `POST /abort`

Tras cancelar desde el navegador:

```bash
docker compose logs simulador-pi watcher | grep abort
```
```
[simulador] captura 20260928T195410Z_sim01 abortada a los 6.1 s, JSONL borrado
[simulador] transfer: 20260928T195410Z_sim01: abort registrado en el watcher
watcher abort 20260928T195410Z_sim01 OK (created -> error): pk=1, 6.1 s capturados
```

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "SELECT session_id, status, status_detail, duration_sec, checksum FROM sessions" \
  -c "SELECT session_pk, from_status, to_status, detail FROM session_events"
```

La sesión está en `error` con `abort: cancelada por el usuario a los 6 s`, la
duración parcial y sin checksum; el historial tiene `created` y
`created -> error`.

**Watcher caído**: con `docker compose stop watcher`, cancelar una captura
vuelve a bienvenida igual (el aviso tarda como mucho 3 s) y el aviso queda en
`/tmp/ppg/pending/<session_id>.abort.json`. Tras `docker compose start watcher`,
la siguiente sesión lo reintenta antes de capturar
(`reintentando el abort desde pending/` → `abort registrado en el watcher`).

Casos a mano:

```bash
ab() { curl -s -w " [%{http_code}]\n" "$@" localhost:5001/abort; }
```

| Prueba | Comando | Esperado |
|--------|---------|----------|
| Reintento del mismo abort | `ab -F session_id=<abortada> -F duration=6` | `200`, `"outcome":"reintento"` |
| Sesión ya subida | `ab -F session_id=<subida> -F duration=6` | `409 la sesión ya está en '...'` |
| `session_id` con `..` | `ab -F session_id=../x -F duration=1` | `400 session_id inválido` |
| Sin `duration` | `ab -F session_id=prueba_x` | `400` |
| Sesión en `created` | insertar `prueba_created` como en la sección 5 y `ab -F session_id=prueba_created -F duration=12.5` | `200`, `"outcome":"created -> error"` |

## 8. Fase 10: limpieza

Con sesiones de 15 s (con 8 s Spark no junta 10 intervalos RR y la sesión
termina en `error`):

```bash
SIM_DURATION_SEC=15 docker compose up -d simulador-pi
```

**Crudo borrado tras el análisis.** Hacer una sesión completa. Al quedar en
`ready`, el log del watcher dice `<session_id>: crudo borrado` y
`/data/raw/` solo tiene `.incoming/`:

```bash
docker compose logs watcher | grep cleanup
docker compose exec watcher ls -A /data/raw
```

Una sesión que termina en `error` (por ejemplo, de 8 s) conserva su crudo.

**Retención de 10 sesiones.** Hacer más de 10 sesiones, mezclando completas y
canceladas. Desde la undécima, cada sesión que termina (también un abort)
borra la más vieja:

```
watcher.cleanup retención: 1 sesión(es) viejas borradas: 20260928T202010Z_sim01
```

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "SELECT id, session_id, status FROM sessions ORDER BY start_time" \
  -c "SELECT (SELECT count(*) FROM metrics) m, (SELECT count(*) FROM peaks) p,
             (SELECT count(*) FROM stress_windows) w, (SELECT count(*) FROM session_events) e"
```

Quedan 10 filas y métricas, picos, ventanas e historial solo de esas 10 (ON
DELETE CASCADE). Si la sesión borrada estaba en `error` con crudo, también se
borra el crudo. Las sesiones que siguen en curso (`created`, `uploaded`,
`processing`) nunca se borran, y la que acaba de terminar tampoco aunque su
`start_time` sea más viejo que las otras 10 (un reintento desde `pending/`):
sale en la siguiente limpieza.

**Barrido al arrancar.** Dejar basura y reiniciar el watcher:

```bash
docker compose exec watcher sh -c 'echo x > /data/raw/huerfano.jsonl;
  echo x > /data/raw/.incoming/foo.abc.part;
  echo x > /data/raw/<session_id en ready>.jsonl'
docker compose restart watcher
docker compose logs --since 30s watcher | grep cleanup
```

```
watcher.cleanup barrido: temporal foo.abc.part borrado
watcher.cleanup <session_id>: crudo borrado
watcher.cleanup barrido: huerfano.jsonl sin sesión en la base, borrado
```

El número máximo se cambia con la variable `MAX_SESSIONS` del watcher.

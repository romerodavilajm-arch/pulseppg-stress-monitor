# Pruebas de la Fase 4: transferencia del JSONL al watcher

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
a `aborted`, no aparece ningún archivo nuevo en `/data/raw/` ni fila nueva en
`sessions`, y en el simulador no queda el JSONL de esa sesión
(`find /tmp/ppg -type f`).

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

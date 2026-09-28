# Pruebas de la Fase 5: watchdog + Spark

Guía manual para verificar que, cuando un JSONL llega a `/data/raw/`, el
watcher lo detecta, pasa la sesión a `processing`, lanza un contenedor efímero
de Spark que escribe `metrics` y `peaks` en PostgreSQL, y deja la sesión en
`ready` (o en `error` si Spark falla dos veces). Se usa el simulador como Pi.

## 1. Levantar todo

```bash
SIM_DURATION_SEC=60 docker compose up -d --build
docker compose ps -a
```

- `postgres`, `servidor-web` y `watcher`: `healthy`; `simulador-pi`: `Up`.
- `spark`: `Exited (0)`. Es lo esperado: en `compose up` solo construye la
  imagen `pulseppg-spark` y comprueba que arranca
  (`docker compose logs spark` → `[spark] imagen lista, pyspark 4.0.1`).
  El watcher no arranca si esa comprobación falla.

Al arrancar, el watcher anuncia que vigila la carpeta:

```bash
docker compose logs watcher
```
```
watcher.runner análisis en la red pulseppg_default con el volumen pulseppg_rawdata
watcher.watch vigilando /data/raw
```

## 2. Una sesión llega a `ready` sola

1. Abrir `http://localhost:5000` y presionar "Comenzar prueba". Esperar los
   60 s de captura.
2. Logs del watcher (Spark tarda unos 12 s, casi todo en arrancar la JVM):
   ```bash
   docker compose logs -f watcher
   ```
   ```
   watcher.watch archivo nuevo en /data/raw/: 20260928T075204Z_sim01.jsonl
   watcher upload 20260928T075204Z_sim01 OK (nueva): pk=1, 3000 muestras, ...
   watcher.runner 20260928T075204Z_sim01: 'processing' (pk=1)
   watcher.runner   [spark] {"samples": 3000, "duration_sec": 59.98, "fs_hz": 50.0, "peaks": 71, "rr_valid": 70, "bpm": 71.97, "sdnn": 32.58, "rmssd": 40.58, "pnn50": 20.29}
   watcher.runner spark terminó en 12.0 s (intento 1)
   watcher.runner 20260928T075204Z_sim01: 'ready'
   ```
   El simulador late a 72 BPM con una oscilación de ±4 BPM, así que BPM debe
   salir ≈ 72 y SDNN/RMSSD en torno a 30–40 ms (no cero).
3. En PostgreSQL:
   ```bash
   docker compose exec postgres psql -U pulseppg -d pulseppg \
     -c "SELECT id, session_id, status, status_detail FROM sessions" \
     -c "SELECT session_pk, from_status, to_status, detail FROM session_events" \
     -c "SELECT * FROM metrics" \
     -c "SELECT count(*) AS picos, count(rr_interval) AS con_rr FROM peaks" \
     -c "SELECT t, rr_interval FROM peaks ORDER BY t LIMIT 5"
   ```
   - La sesión en `ready`, con el historial `uploaded → processing → ready`.
   - Una fila en `metrics`.
   - ~72 picos por minuto en `peaks`; el primero sin `rr_interval`.
4. No queda ningún contenedor de Spark colgado:
   ```bash
   docker ps -a --filter label=pulseppg.step
   ```
5. El crudo sigue en `/data/raw/` (el borrado llega con la limpieza, en una
   fase posterior):
   ```bash
   docker compose exec watcher ls -l /data/raw
   ```

## 3. Spark por separado

Sobre cualquier archivo que ya esté en `/data/raw/`:

```bash
# Solo imprime, no escribe en la base
docker compose run --rm spark python3 /app/hrv.py /data/raw/<session_id>.jsonl --no-db
# Escribe para la sesión <pk> (sessions.id)
docker compose run --rm spark python3 /app/hrv.py /data/raw/<session_id>.jsonl <pk>
```

Correrlo dos veces sobre la misma sesión deja el mismo número de filas en
`peaks` y una sola en `metrics`: reemplaza, no acumula.

Códigos de salida (`echo $?`): `0` OK, `2` entrada inválida (archivo sin
muestras, menos de 10 latidos, sesión inexistente), `1` error inesperado.

## 4. Spark falla: reintento y `error`

Una señal plana no tiene latidos:

```bash
python3 -c "
import json
for i in range(3000): print(json.dumps({'t': i/50, 'ir': 110000, 'red': 80000}))" > plano.jsonl
curl -s -F session_id=plano -F checksum=$(sha256sum plano.jsonl | cut -d' ' -f1) \
     -F duration=60 -F quality=mala -F file=@plano.jsonl localhost:5001/upload
```

En los logs del watcher: el primer intento falla, espera 3 s, reintenta y la
sesión pasa a `error` con el motivo; el archivo se conserva.

```
watcher.runner plano: spark falló (código 2: [spark] ... solo 0 intervalos RR válidos en 60.0 s (0 picos); se necesitan 10), reintento en 3 s
watcher.runner plano: spark falló dos veces, sesión en 'error'; se conserva /data/raw/plano.jsonl
```

```bash
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "SELECT session_id, status, status_detail FROM sessions WHERE session_id = 'plano'"
```

## 5. Recuperación al arrancar

Una sesión que quedó en `uploaded` (o `processing`) con el watcher caído se
procesa cuando vuelve:

```bash
docker compose stop watcher
# Copiar un crudo existente con otro nombre y registrarlo como recibido
docker compose run --rm --no-deps --entrypoint "" -v rawdata:/w -u root spark \
  cp /data/raw/<session_id>.jsonl /w/recuperada.jsonl
docker compose exec postgres psql -U pulseppg -d pulseppg \
  -c "INSERT INTO sessions (session_id, start_time, status) VALUES ('recuperada', now(), 'uploaded')"
docker compose start watcher
docker compose logs -f watcher
```
```
watcher.runner recuperación: recuperada estaba en 'uploaded'
watcher.runner recuperada: 'processing' (pk=3)
...
watcher.runner recuperada: 'ready'
```

## 6. Limpiar

```bash
docker compose down        # conserva la base y /data/raw
docker compose down -v     # borra también los volúmenes pgdata y rawdata
```

## Nota: el watcher controla Docker

Para lanzar Spark como contenedor hermano, el watcher monta
`/var/run/docker.sock`. Es lo que permite el `compose run --rm spark` de la
propuesta, pero equivale a darle control total de Docker en la PC: está bien
para la demo en red local, no para un servidor compartido.

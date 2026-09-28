# Pruebas del servidor-web: fases 2 y 8

Guía manual para verificar que las muestras viajan de la Pi (o del simulador) al
navegador, que `start` y `abort` viajan en sentido contrario (Fase 2) y que la
pantalla única recorre sus 4 estados sin intervención manual (Fase 8,
secciones 7 a 9).

## 1. Levantar todo

```bash
docker compose up -d --build
```

Para no esperar los 5 minutos de una sesión real, se puede acortar la sesión
del simulador:

```bash
SIM_DURATION_SEC=20 docker compose up -d --build
```

Comprobar el estado:

```bash
docker compose ps
```

`postgres` y `servidor-web` deben estar `healthy`; `simulador-pi`, `Up`.

## 2. El simulador está registrado

```bash
curl -s localhost:5000/health
```

Debe devolver `{"ok":true,"pi_connected":true}`. En los logs:

```bash
docker compose logs servidor-web simulador-pi
```

Deben aparecer `[simulador] conectado a http://servidor-web:5000` y
`Pi registrada`.

## 3. Ver la onda en el navegador

1. Abrir `http://localhost:5000` (o `http://<ip-pc>:5000` desde otro equipo).
2. Debe decir **Pi: conectada** en verde y el botón "Comenzar prueba" activo.
3. Presionar "Comenzar prueba".
4. La pantalla cambia a captura en vivo:
   - La onda IR se dibuja con latidos (pico + muesca dicrótica) a unos 72 BPM.
   - Tiempo restante baja cada segundo; Pulso muestra unos 72 tras 4 s;
     Calidad dice "buena".
   - El contador de muestras sube unas 50 por segundo. Con
     `SIM_DURATION_SEC=8` llega a 400 exactas (8 s × 50 Hz).
5. Al terminar la sesión la pantalla pasa a "Enviando la captura..." y enseguida
   a "Analizando..." (sección 8).

## 4. Cancelar con `abort`

1. Presionar "Comenzar prueba" y, a los pocos segundos, "Cancelar".
2. La página vuelve a bienvenida y aparece "Prueba cancelada.".
3. En los logs del servidor-web:
   ```
   Reenviando abort a la Pi
   Pi status: aborted
   ```
4. "Comenzar prueba" vuelve a estar activo y se puede repetir.

## 5. Reconexión

```bash
docker compose restart servidor-web
docker compose logs --tail 5 simulador-pi
```

El simulador debe mostrar `desconectado, reintentando...` y, unos segundos
después, `conectado a ...` de nuevo. Al recargar la página vuelve a decir
**Pi: conectada**.

Al revés, `docker compose stop simulador-pi` debe poner la página en
**Pi: desconectada** sin recargarla y deshabilitar "Comenzar prueba".

## 6. Usar la Pi real en lugar del simulador

Levantar solo los servicios del PC:

```bash
docker compose up -d postgres servidor-web watcher
```

La Pi se conecta a `http://<ip-pc>:5000` con Socket.IO, envía `register_pi {}`
y a partir de ahí habla el mismo protocolo que `simulador/simulador.py`
(sección 8 de `docs/Propuesta técnica.md`).

## 7. Fase 8 aislada: resultados con datos sembrados

Solo PostgreSQL y servidor-web, sin Pi, watcher, Spark ni modelo:

```bash
docker compose up -d --build postgres servidor-web
docker compose exec -T postgres psql -q -U pulseppg -d pulseppg <<'SQL'
BEGIN;
INSERT INTO sessions (session_id, device_id, start_time, duration_sec, quality, status)
VALUES ('20260928T120000Z_demo', 'demo', '2026-09-28T12:00:00Z', 300, 'simulada', 'uploaded');
UPDATE sessions SET status = 'processing' WHERE session_id = '20260928T120000Z_demo';
INSERT INTO metrics (session_pk, bpm, sdnn, rmssd, pnn50)
SELECT id, 72.4, 48.1, 35.2, 12.5 FROM sessions WHERE session_id = '20260928T120000Z_demo';
INSERT INTO stress_windows (session_pk, t_start, t_end, level, score)
SELECT s.id, g * 30, g * 30 + 30,
       CASE WHEN g IN (3, 4, 7) THEN 'estres' ELSE 'sin_estres' END,
       CASE WHEN g IN (3, 4, 7) THEN 0.7 ELSE 0.2 END
  FROM sessions s, generate_series(0, 9) g WHERE s.session_id = '20260928T120000Z_demo';
UPDATE sessions SET status = 'ready' WHERE session_id = '20260928T120000Z_demo';
COMMIT;
SQL
```

Las APIs:

```bash
curl -s localhost:5000/api/sesiones
curl -s localhost:5000/api/estado/20260928T120000Z_demo
curl -s localhost:5000/api/resultados/20260928T120000Z_demo
curl -si localhost:5000/api/estado/no-existe | head -1     # 404
```

`/api/estado` debe decir `"status":"ready"` y `/api/resultados` traer las
métricas, 10 ventanas y `"summary": {"level": "bajo", "estres": 3, ...}`
(3 de 10 es menos de 1/3).

En `http://localhost:5000`, bajo "Sesiones anteriores" aparece la sesión con
72 BPM. "Ver" abre los resultados: BPM 72, SDNN 48 ms, RMSSD 35 ms, Estrés
Bajo y la línea de tiempo con 10 barras, rojas en 1:30–2:30 y 3:30–4:00. "Ver
detalle técnico" muestra la sesión, las ventanas y el historial
`uploaded → processing → ready`. "Nueva sesión" vuelve a bienvenida.

## 8. Fase 8 de punta a punta: sin intervención manual

Con todo levantado (`SIM_DURATION_SEC=65 docker compose up -d --build`, 2
ventanas de 30 s):

1. Abrir `http://localhost:5000` y presionar "Comenzar prueba". Es el único
   clic hasta ver los resultados.
2. Captura en vivo durante 65 s. Recargar la página a mitad debe volver a la
   captura, no a bienvenida.
3. "Enviando la captura..." y luego "Analizando..." con el paso en curso
   (`processing`). Recargar aquí también vuelve a "Analizando...".
4. Cuando el watcher deja la sesión en `ready` (unos 15 s tras la subida), la
   pantalla cambia sola a resultados: BPM ≈ 72, SDNN ≈ 31 ms, línea de tiempo
   con 2 barras. Con la señal simulada el nivel de estrés no significa nada.
5. "Nueva sesión" vuelve a bienvenida y la sesión aparece en la lista.

En los logs:

```bash
docker compose logs servidor-web watcher | grep -E "status|ready"
```

```
servidor-web Pi status: capturing (20260928T151400Z_sim01)
servidor-web Pi status: uploading (20260928T151400Z_sim01)
servidor-web Pi status: done (20260928T151400Z_sim01)
watcher.runner 20260928T151400Z_sim01: 'ready'
```

## 9. Sesión que termina en `error`

Si Spark o el modelo fallan dos veces, la pantalla de resultados muestra "La
sesión no se pudo analizar" con el motivo que dejó el watcher (por ejemplo
`modelo: código 2: falta el clasificador`) y el historial de estados en el
detalle técnico. "Nueva sesión" vuelve a bienvenida.

Para provocarlo, mover el clasificador y reconstruir el modelo:

```bash
mv modelo/artefactos/clasificador.joblib /tmp/
docker compose build modelo
# ...hacer una prueba completa y ver el error...
mv /tmp/clasificador.joblib modelo/artefactos/ && docker compose build modelo
```

## 10. Si algo falla

| Síntoma | Qué revisar |
|---------|-------------|
| La página no carga | `docker compose logs servidor-web`; ¿el puerto 5000 está ocupado en el PC? |
| Pi: desconectada | `docker compose logs simulador-pi`; debe reintentar cada pocos segundos |
| Pi conectada pero no se dibuja nada | Consola del navegador (F12); ¿llegan eventos `sample`? |
| Muestras sube mucho menos de 50/s | Carga del PC; el servidor usa hilos, no está pensado para muchos navegadores |
| Se queda en "Analizando..." | `docker compose logs watcher`; si tras 60 s el watcher no conoce la sesión la página lo dice |
| `/api/...` responde 503 | PostgreSQL no está arriba: `docker compose ps postgres` |

## 11. Apagar

```bash
docker compose down
```

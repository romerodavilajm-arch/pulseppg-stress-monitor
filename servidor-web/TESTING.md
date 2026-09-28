# Pruebas de la Fase 2: WebSocket Pi ↔ servidor-web ↔ navegador

Guía manual para verificar que las muestras viajan de la Pi (o del simulador) al
navegador y que `start` y `abort` viajan en sentido contrario.

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
4. Debe ocurrir:
   - Estado: `capturing`.
   - La onda IR se dibuja con latidos (pico + muesca dicrótica) a unos 72 BPM.
   - El tiempo baja cada segundo.
   - Muestras sube unas 50 por segundo.
5. Al terminar la sesión: Estado `uploading` y enseguida `done` (el JSONL ya está en el watcher, ver `watcher/TESTING.md`), Tiempo `0:00`. Con `SIM_DURATION_SEC=8`
   el contador llega a 400 muestras exactas (8 s × 50 Hz).

## 4. Cancelar con `abort`

1. Presionar "Comenzar prueba" y, a los pocos segundos, "Cancelar".
2. La onda se detiene, Estado pasa a `aborted` y aparece "Prueba cancelada.".
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

## 7. Si algo falla

| Síntoma | Qué revisar |
|---------|-------------|
| La página no carga | `docker compose logs servidor-web`; ¿el puerto 5000 está ocupado en el PC? |
| Pi: desconectada | `docker compose logs simulador-pi`; debe reintentar cada pocos segundos |
| Pi conectada pero no se dibuja nada | Consola del navegador (F12); ¿llegan eventos `sample`? |
| Muestras sube mucho menos de 50/s | Carga del PC; el servidor usa hilos, no está pensado para muchos navegadores |

## 8. Apagar

```bash
docker compose down
```

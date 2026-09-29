# Propuesta técnica: Sistema de adquisición PPG y clasificación de estrés

---

## 1. Resumen ejecutivo

Sistema que captura señal fotopletismográfica (PPG) durante 5 minutos con hardware de bajo costo, la transfiere de forma confiable a un equipo de análisis, calcula métricas de variabilidad de frecuencia cardíaca (HRV) y clasifica el nivel de estrés con un modelo preentrenado, presentando los resultados en una interfaz web única.

**Frase que resume el diseño**: *"La Pi captura y transfiere. El watcher recibe y coordina. Spark y el modelo analizan. PostgreSQL guarda. El servidor web muestra."*

---

## 2. Problema

El estrés sostenido altera el sistema nervioso autónomo y se refleja en la variabilidad de la frecuencia cardíaca. Los métodos clínicos para medirlo son costosos, invasivos o no están disponibles en entornos cotidianos.

Existen herramientas parciales:

- **Toolkits de software** (pvPPG, HeartPy, NeuroKit2): asumen datos ya limpios, no gestionan hardware, no ofrecen interfaz final.
- **Apps de smartphone (rPPG)**: dependen de la cámara, cuya calidad es sensible a iluminación y movimiento.
- **Soluciones comerciales**: son cajas negras; firmware y algoritmos no son inspeccionables, los datos no son exportables en formato abierto.

**No existe una plataforma abierta, con hardware de contacto dedicado, que integre adquisición, análisis HRV, clasificación de estrés y auditoría de cada etapa en un mismo flujo reproducible.**

---

## 3. Alcance

### Incluye

| Dimensión | Detalle |
|-----------|---------|
| **Hardware** | MAX30102 (I2C, 50 Hz) + Raspberry Pi 5 |
| **Captura** | Sesión de 5 minutos con validación de calidad en tiempo real |
| **Transferencia** | HTTP POST con checksum, 3 reintentos, respaldo local |
| **Procesamiento** | Cálculo de BPM, SDNN, RMSSD, pNN50 (Spark) |
| **Clasificación** | Modelo Pulse-PPG preentrenado, ventanas de 30 s |
| **Visualización** | Pantalla única: bienvenida, captura en vivo, análisis, resultados |
| **Persistencia** | PostgreSQL con las últimas 10 sesiones |
| **Reproducibilidad** | Compose, un comando levanta el sistema |
| **Confiabilidad** | Máquina de estados, idempotencia, reintentos acotados |

### No incluye

- Uso clínico o diagnóstico médico.
- Validación con población amplia.
- Entrenamiento del modelo (se usa preentrenado).
- Tiempo real estricto durante la captura.
- Multi-usuario, autenticación, conectividad a la nube.
- Calibración individual por línea base del usuario.

### Métrica de éxito

| Criterio | Objetivo |
|----------|----------|
| Trazabilidad | Cada resultado se rastrea hasta la muestra cruda que lo originó |
| Modificabilidad | Cambiar parámetros de análisis sin recompilar |
| Reproducibilidad | `compose up` en máquina limpia produce el mismo resultado |
| Recuperación | Fallas transitorias se resuelven sin intervención manual |
| Claridad | Cada componente se explica en una frase |

---

## 4. Estrategia de solución

El sistema se divide en **cinco etapas con ciclos de vida distintos**, cada una en su propio contenedor:

1. **Adquisición** (Pi): captura y transfiere.
2. **Coordinación** (watcher): recibe, dispara, limpia.
3. **Análisis** (Spark y modelo): efímeros, leen archivo, escriben base.
4. **Persistencia** (PostgreSQL): resultados.
5. **Presentación** (servidor web): muestra en vivo y resultados.

### Decisiones clave

| Decisión | Alternativa descartada | Justificación |
|----------|------------------------|---------------|
| HTTP POST con checksum | NFS, Samba, scp | Confirmación a nivel de aplicación, sin montar filesystems |
| Archivo JSONL como crudo | Insertar en base | Spark lo lee nativo, sin inflar PostgreSQL |
| Watchdog sobre carpeta | Polling periódico | Reacción en milisegundos, sin consumo en reposo |
| Máquina de estados explícita | Campos sueltos | Elimina fallas silenciosas, permite recuperación |
| Servidor web pasivo | Servidor que persiste | Separación de responsabilidades, robustez |
| Análisis batch | Spark Streaming | El caso de uso no requiere tiempo real |
| Modelo preentrenado | Entrenar propio | No hay dataset etiquetado propio |
| 10 sesiones retenidas | Sin límite | La demo no requiere histórico extenso |
| Borrado del crudo tras análisis | Conservar | Ahorra disco; el respaldo vive en la Pi en `tmp` |
| JSONL con `t, ir, red` | CSV, Parquet, HDF5 | Simple, inspeccionable, nativo en Spark |
| `compose.yaml` | `docker-compose.yml` | Estándar vigente desde 2024 |

---

## 5. Arquitectura

### Diagrama de componentes

```
┌─────────────────────────────────────────────────────────────┐
│  RASPBERRY PI 5                                              │
│                                                              │
│  MAX30102 ──I2C──▶ buffer ──┬──▶ WebSocket (dibujo en vivo) │
│                             │                                │
│                             └──▶ /tmp/ppg/<id>.jsonl         │
│                                                              │
│  Al terminar:                                                │
│    · SHA-256 del archivo                                     │
│    · POST /upload al watcher                                 │
│    · Si 200 OK → mover a /tmp/ppg/completed/                 │
│    · Si falla  → mover a /tmp/ppg/pending/                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP POST + WebSocket
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  PC — Compose                                                │
│                                                              │
│  ┌────────────────────┐    ┌──────────────────────────────┐ │
│  │ servidor-web       │    │ postgres                     │ │
│  │ Flask + SocketIO   │    │ Resultados + estados         │ │
│  │ :5000              │    │ (máx. 10 sesiones)           │ │
│  │                    │    │                              │ │
│  │ · Recibe WebSocket │    │                              │ │
│  │ · Reenvía al nav.  │    │                              │ │
│  │ · Envía abort      │    │                              │ │
│  │ · Consulta SQL     │    │                              │ │
│  │ · No guarda nada   │    │                              │ │
│  └────────────────────┘    └──────────────┬───────────────┘ │
│                                             │                │
│  ┌──────────────────────────────────────────┴─────────────┐ │
│  │ watcher                                                 │ │
│  │ :5001                                                    │ │
│  │                                                          │ │
│  │ · HTTP POST /upload → guarda en /data/raw/              │ │
│  │ · Watchdog vigila /data/raw/                            │ │
│  │ · Al detectar: lanza Spark y modelo                     │ │
│  │ · Espera códigos de salida                              │ │
│  │ · Actualiza estado de sesión                            │ │
│  │ · Borra archivo crudo                                   │ │
│  │ · Limpia sesiones viejas (máx. 10)                      │ │
│  └──────────────────────────────────────────┬─────────────┘ │
│                                             │                │
│                              ┌──────────────┴──────────┐    │
│                              │ spark (efímero)         │    │
│                              │ modelo (efímero)        │    │
│                              └─────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Contenedores

| Contenedor | Ciclo de vida | Rol |
|------------|---------------|-----|
| `postgres` | Permanente | Persistencia de resultados |
| `servidor-web` | Permanente | Interfaz web + WebSocket |
| `watcher` | Permanente | Recepción, coordinación, limpieza |
| `spark` | Efímero | Cálculo HRV |
| `modelo` | Efímero | Clasificación de estrés |

**3 permanentes + 2 efímeros.**

### Fuera de Compose

| Componente | Rol |
|------------|-----|
| Raspberry Pi 5 | Captura, validación local, transferencia |
| MAX30102 | Sensor PPG por I2C |
| Navegador | Interfaz de usuario |

---

## 6. Flujo completo de una sesión

### Fase 0 — Setup

- PostgreSQL con esquema inicializado.
- servidor-web escuchando en `:5000`.
- watcher escuchando en `:5001`, watchdog vigilando `/data/raw/`.
- Pi conectada por WebSocket, esperando `start`.
- Navegador cerrado.

### Fase 1 — Bienvenida

1. Usuario abre `http://<ip-pc>:5000`.
2. servidor-web sirve la pantalla con instrucciones.
3. Navegador muestra "Presiona Comenzar".

### Fase 2 — Inicio

1. Usuario presiona "Comenzar prueba".
2. Navegador → servidor-web: `start`.
3. servidor-web:
   - Inserta sesión en PostgreSQL con estado `created`.
   - Reenvía `start` a la Pi.
4. Pi:
   - Abre `/tmp/ppg/<id>.jsonl`.
   - Empieza a leer el sensor.
   - Empieza a emitir samples por WebSocket.

### Fase 3 — Captura (5 minutos)

**Cada muestra** (50 Hz):

- Pi escribe línea en el JSONL.
- Pi emite `sample` por WebSocket.
- servidor-web reenvía al navegador.
- Navegador dibuja la onda.

**Cada segundo**:

- Pi emite `countdown`.
- servidor-web reenvía.
- Navegador actualiza contador.

**Eventos de calidad**:

| Evento | Detección | Acción |
|--------|-----------|--------|
| Dedo retirado | IR < 50000 | Pausa, mensaje, espera recolocación, reinicia sesión |
| Movimiento | Varianza > umbral | Reinicia sesión directamente |
| WebSocket caído < 5 s | Timeout de conexión | Pi sigue capturando, reconecta |
| WebSocket caído > 5 s | Timeout sostenido | Pi reinicia sesión |

**Cancelación por el usuario**:

En cualquier momento durante la captura, el navegador puede enviar `abort` al servidor-web. Este reenvía el mensaje a la Pi. La Pi:

1. Detiene la captura inmediatamente.
2. Cierra el JSONL.
3. **Borra el archivo local** (no se transfiere, no se analiza).
4. Avisa al watcher con `POST /abort` (session_id y segundos capturados).
5. Emite `status: aborted`.
6. Vuelve al estado de espera.

El watcher marca la sesión en PostgreSQL como `error` con la duración parcial y `status_detail` "abort: ...", y el navegador vuelve a la pantalla de bienvenida, donde la sesión aparece como cancelada. Lo registra el watcher y no el servidor-web para que este siga siendo pasivo (solo lee) y porque el watcher ya es quien crea las sesiones.

**Motivo del abort**: el usuario decide cancelar antes de terminar los 5 minutos. Casos típicos: se siente incómodo, se equivocó al iniciar, necesita interrumpir. El sistema no debe procesar datos incompletos.

### Fase 4 — Transferencia

1. Pi cierra el JSONL.
2. Pi calcula SHA-256.
3. Pi hace `POST /upload` al watcher con archivo, checksum y metadata.
4. Watcher:
   - Verifica idempotencia por `session_id`.
   - Guarda en `/data/raw/<id>.jsonl`.
   - Calcula checksum y compara.
   - Actualiza sesión a `uploaded`.
   - Responde `200 OK`.
5. Pi:
   - Mueve archivo a `/tmp/ppg/completed/`.
   - Emite `done` al servidor-web.
6. Navegador cambia a "Analizando...".

### Fase 5 — Detección y análisis

1. Watchdog detecta el archivo nuevo en `/data/raw/`.
2. Watcher:
   - Actualiza sesión a `processing`.
   - Lanza Spark: `compose run --rm spark python /app/hrv.py /data/raw/<id>.jsonl <session_pk>`.
   - Lanza modelo: `compose run --rm modelo python /app/infer.py /data/raw/<id>.jsonl <session_pk>`.
   - Espera códigos de salida de ambos.
3. Spark:
   - Lee JSONL.
   - Detecta picos.
   - Calcula BPM, SDNN, RMSSD, pNN50.
   - Inserta en `metrics` y `peaks`.
   - Termina con código 0.
4. Modelo:
   - Lee JSONL.
   - Segmenta en ventanas de 30 s.
   - Calcula embeddings e infiere.
   - Inserta en `stress_windows`.
   - Termina con código 0.

**Si Spark o el modelo fallan**:

- Watcher espera 3 segundos.
- Reintenta una vez.
- Si vuelve a fallar → sesión a `error`, archivo se conserva.

### Fase 6 — Limpieza

1. Watcher actualiza sesión a `ready`.
2. Watcher borra `/data/raw/<id>.jsonl`.
3. Watcher ejecuta limpieza de sesiones viejas:

```sql
DELETE FROM sessions
WHERE id NOT IN (
    SELECT id FROM sessions ORDER BY start_time DESC, id DESC LIMIT 10
)
AND id != <session_pk_actual>
AND status IN ('ready', 'error');
```

La limpieza también corre tras un `abort` (las canceladas quedan en `error`) y
al arrancar el watcher, que además borra temporales de `.incoming/`, el crudo
de sesiones ya listas y archivos sin sesión. Si la sesión termina en `error`,
su crudo se conserva hasta que la sesión sale de las 10 retenidas.

### Fase 7 — Resultados

1. Navegador consulta `GET /api/estado/<session_id>` cada 2 s.
2. Cuando el estado es `ready`, navegador consulta `GET /api/resultados/<session_id>`.
3. servidor-web responde con BPM, SDNN, RMSSD, estado global y ventanas.
4. Navegador cambia a pantalla de resultados.

### Fase 8 — Nueva sesión

1. Usuario presiona "Nueva sesión".
2. Navegador → servidor-web: `new`.
3. Navegador vuelve a bienvenida.
4. Pi borra `/tmp/ppg/completed/`.
5. Pi reintenta lo que esté en `/tmp/ppg/pending/`.
6. Ciclo se repite.

---

## 7. Máquina de estados

```
Estado        Quién lo establece          Cuándo
──────────────────────────────────────────────────────────
created       servidor-web                Al recibir "start"
uploaded      watcher                     Al recibir el POST
processing    watcher                     Antes de lanzar Spark
ready         watcher                     Tras Spark y modelo OK
error         watcher                     Si Spark o modelo fallan 2 veces
                                           o si el usuario aborta (POST /abort)
```

**Transiciones válidas**:

```
created → uploaded → processing → ready
   ↓                       ↓
 error                   error
```

**Beneficios**:

- El servidor-web sabe qué mostrar en cada momento.
- El watcher sabe qué hacer al arrancar (buscar `uploaded` o `processing`).
- El navegador sabe cuándo dejar de hacer polling.
- Fallas silenciosas se convierten en estados explícitos.

---

## 8. Protocolos

### WebSocket — Pi ↔ servidor-web

**Pi → servidor-web**:

| Mensaje | Payload |
|---------|---------|
| `sample` | `{t, ir, red}` |
| `countdown` | `{value}` |
| `status` | `{value: capturing \| paused \| restarting \| uploading \| done \| upload_failed \| aborted, session_id}` |
| `register_pi` | `{}` |

**servidor-web → Pi**:

| Mensaje | Payload |
|---------|---------|
| `start` | `{}` |
| `abort` | `{}` |

**Nota sobre `uploading`, `done` y `upload_failed`**: al terminar la captura la Pi emite `uploading` mientras hace el POST al watcher, y `done` cuando el watcher confirma (200 o 409). Si los 3 intentos fallan emite `upload_failed`; el archivo queda en `/tmp/ppg/pending/` y se reintenta antes de la siguiente sesión.

**Nota sobre `session_id` en `status`**: la Pi genera el `session_id` y lo incluye en cada `status`. Así el servidor-web puede avisar `analyzing {session_id}` al navegador cuando llega `done`, y el navegador sabe qué sesión consultar en `/api/estado/<session_id>`.

**Nota sobre `abort`**: el navegador puede enviarlo en cualquier momento durante la captura. El servidor-web lo reenvía a la Pi. La Pi detiene la captura, borra el archivo local, avisa al watcher con `POST /abort` (la sesión queda en `error`), emite `status: aborted` y vuelve a esperar.

**servidor-web → Navegador**:

| Mensaje | Payload |
|---------|---------|
| `sample` | `{t, ir, red}` |
| `countdown` | `{value}` |
| `status` | `{value}` |
| `analyzing` | `{session_id}` |
| `results_ready` | No se usa: el navegador detecta `ready` consultando `/api/estado` (fase 7 del flujo) |
| `aborted` | `{}` |
| `error` | `{message}` |
| `new` | `{}` (todos los navegadores vuelven a bienvenida) |

**Navegador → servidor-web**:

| Mensaje | Payload |
|---------|---------|
| `start` | `{}` |
| `abort` | `{}` |
| `new` | `{}` |

### HTTP — Pi ↔ watcher

**POST /upload**

```
Content-Type: multipart/form-data

Campos:
  - session_id: string (único, formato <timestamp>_<device_id>)
  - checksum: string (SHA-256)
  - duration: float
  - quality: string
  - device_id: string (opcional)
  - start_time: ISO 8601 (opcional; si falta, ahora - duration)
  - file: archivo JSONL
```

**Respuestas**:

| Código | Significado |
|--------|-------------|
| 200 | Archivo recibido y checksum verificado |
| 400 | Checksum no coincide |
| 409 | session_id ya existe en estado posterior (idempotencia) |
| 500 | Error del watcher |

**Reintentos en la Pi**: 3 intentos con backoff de 0 s, 2 s, 5 s.

**POST /abort** (Fase 9)

```
Content-Type: multipart/form-data (o urlencoded)

Campos:
  - session_id: string
  - duration: float (segundos capturados antes de cancelar)
  - device_id: string (opcional)
  - start_time: ISO 8601 (opcional; si falta, ahora - duration)
```

El watcher crea la sesión en `created` si no existía y la pasa a `error` en la misma transacción (el historial registra `created` y `created → error`).

| Código | Significado |
|--------|-------------|
| 200 | Sesión en `error` (también si ya estaba abortada: reintento) |
| 400 | Campos inválidos |
| 409 | La sesión ya está en otro estado (el abort llegó tarde) |
| 500 | Error del watcher |

**Reintentos en la Pi**: un solo intento de 3 s antes de emitir `aborted`, para no hacer esperar al usuario. Si falla, el aviso queda en `/tmp/ppg/pending/<session_id>.abort.json` y se reintenta junto con los uploads pendientes.

### HTTP — Navegador ↔ servidor-web

| Endpoint | Método | Propósito |
|----------|--------|-----------|
| `/` | GET | Pantalla única |
| `/api/estado/<session_id>` | GET | Estado actual de la sesión |
| `/api/resultados/<session_id>` | GET | Métricas y ventanas |
| `/api/sesiones` | GET | Últimas 10 sesiones |
| `/health` | GET | Healthcheck |

---

## 9. Esquema de base de datos

```sql
CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    start_time TIMESTAMP NOT NULL,
    duration_sec REAL,
    quality TEXT,
    device_id TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE metrics (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    bpm REAL,
    sdnn REAL,
    rmssd REAL,
    pnn50 REAL
);

CREATE TABLE peaks (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    t REAL,
    rr_interval REAL
);

CREATE TABLE stress_windows (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    t_start REAL,
    t_end REAL,
    level TEXT,
    score REAL
);

CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_start ON sessions(start_time DESC);
CREATE INDEX idx_peaks_session ON peaks(session_id);
CREATE INDEX idx_windows_session ON stress_windows(session_id);
```

**Notas**:

- `session_id` único evita duplicados por reintentos.
- `ON DELETE CASCADE` simplifica la limpieza.
- `status` implementa la máquina de estados.
- Índices en las consultas más frecuentes.

---

## 10. Formato del archivo crudo

**JSONL** — una línea por muestra:

```json
{"t": 0.020, "ir": 82345, "red": 45231}
{"t": 0.040, "ir": 82401, "red": 45228}
{"t": 0.060, "ir": 82388, "red": 45235}
```

**Metadata** (enviada por POST, no en el archivo):

- `session_id`
- `duration`
- `quality`
- `device_id`

**Elección justificada**:

- Spark lo lee nativo con `spark.read.json()`.
- Python lo lee línea por línea sin dependencias.
- Inspeccionable a mano con `head` o `tail`.
- 5 minutos a 50 Hz → ~600 KB.

---

## 11. Confiabilidad

### Fallas y mitigaciones

| Falla | Probabilidad | Impacto | Mitigación |
|-------|-------------|---------|------------|
| Pi pierde archivo antes de POST | Baja | Catastrófico | Conserva en `/tmp/ppg/` hasta siguiente sesión |
| 200 OK perdido → doble procesamiento | Baja | Medio | Idempotencia por `session_id` |
| Watcher se cae entre recibir y procesar | Baja | Alto | Escaneo al arrancar de estados huérfanos |
| Spark falla | Media | Alto | Reintento una vez, luego `error`, no borra archivo |
| Limpieza borra sesión actual | Baja | Catastrófico | Excluye sesión actual, solo borra `ready` |
| PostgreSQL corrupto | Muy baja | Catastrófico | Volumen nombrado + `restart: unless-stopped` |
| servidor-web se cae | Media-baja | Medio | `restart: unless-stopped` + healthcheck |
| Navegador no ve resultados | Baja | Medio | Estado explícito + polling tolerante a errores |
| Usuario aborta a mitad | Media | Bajo | Mensaje `abort` por WebSocket, limpieza local |
| Movimiento al final de captura | Media | Bajo | Reinicio (decisión de diseño) |
| Relojes desincronizados | Media | Bajo | NTP en Pi y PC |
| Disco lleno en la Pi | Baja | Alto | Verificar antes de capturar |
| Doble procesamiento | Baja | Medio | Constraint único + idempotencia |

### Principios aplicados

1. **Idempotencia**: reintentar no duplica efectos.
2. **Recuperación al arrancar**: los contenedores reintentan lo pendiente.
3. **Estados explícitos**: ninguna sesión queda en limbo.
4. **Fallos acotados**: reintentos limitados, nunca infinitos.
5. **Trazabilidad**: cada sesión tiene logs con `session_id`.
6. **Respaldo local**: la Pi conserva el crudo hasta la siguiente sesión.
7. **Cancelación limpia**: el usuario puede abortar sin dejar residuos.

### Lo que NO se hace

- Kafka (componente sin problema real que resolver).
- Réplica de PostgreSQL (absurdo para 10 sesiones).
- Backup automático (la demo no lo requiere).
- Monitoreo con Prometheus (nadie lo va a mirar).
- Logs externos (Docker logs bastan).
- Autenticación en el POST (red local).

---

## 12. Tecnologías

| Capa | Tecnología | Justificación |
|------|-----------|---------------|
| Hardware | Raspberry Pi 5 + MAX30102 | Bajo costo, disponibilidad, I2C estándar |
| Captura | Python + smbus2 | Nativo en la Pi, sin dependencias pesadas |
| Comunicación en vivo | WebSocket (SocketIO) | Bidireccional, baja latencia |
| Transferencia | HTTP POST + checksum | Confirmación a nivel de aplicación |
| Vigilancia | watchdog | Reactivo, sin polling |
| Procesamiento | Apache Spark | Estándar para análisis de datos, lee JSON nativo |
| Clasificación | Pulse-PPG preentrenado | Estado del arte, no requiere entrenamiento propio |
| Persistencia | PostgreSQL | Escrituras concurrentes, transacciones |
| Servidor web | Flask + Flask-SocketIO | Ligero, suficiente para el caso |
| Frontend | HTML + CSS + JS + Chart.js | Sin framework, fácil de mantener |
| Contenedores | Compose (`compose.yaml`) | Estándar vigente, reproducibilidad, aislamiento |
| Orquestación | Watcher en Python | Simple, control total |

---

## 13. Estructura del proyecto

```
ppg-stress-system/
├── compose.yaml
├── pi/                          # corre en la Raspberry Pi
│   ├── main.py
│   ├── sensor.py
│   ├── quality.py
│   ├── writer.py
│   ├── transfer.py
│   └── display.py
├── watcher/                     # contenedor watcher
│   ├── Dockerfile
│   ├── app.py                   # endpoint /upload
│   ├── watchdog.py              # vigilancia de /data/raw/
│   ├── runner.py                # lanza Spark y modelo
│   └── cleanup.py               # limpieza de sesiones viejas
├── spark/                       # contenedor efímero
│   ├── Dockerfile
│   └── hrv.py
├── modelo/                      # contenedor efímero
│   ├── Dockerfile
│   ├── infer.py
│   └── pulse_ppg/
├── servidor-web/                # contenedor servidor web
│   ├── Dockerfile
│   ├── app.py
│   ├── templates/
│   └── static/
├── db/
│   └── init.sql
└── data/
    ├── raw/                     # archivos crudos (temporales)
    └── pg/                      # datos de PostgreSQL
```

---

## 14. Guion de demo

**"5 minutos de pulso, 20 segundos de análisis, resultados en pantalla"**

1. Usuario se sienta frente a la PC.
2. Abre `http://localhost:5000`.
3. Ve instrucciones y botón "Comenzar prueba".
4. Presiona el botón.
5. La pantalla cambia a captura en vivo:
   - Onda de pulso dibujándose.
   - Contador descendente desde 5:00.
   - Pulso actual en BPM.
   - Indicador de calidad.
   - Botón "Cancelar" disponible por si necesita abortar.
6. A los 5 minutos, la pantalla muestra "Analizando...".
7. A los ~20 segundos, aparece:
   - BPM: 72
   - SDNN: 48 ms
   - RMSSD: 35 ms
   - Estado: Bajo
   - Línea de tiempo con las 10 ventanas clasificadas.
8. Usuario puede presionar "Ver detalle técnico" para auditar.
9. Usuario puede presionar "Nueva sesión" para repetir.

---

## 15. Preguntas abiertas antes de implementar

1. **¿El `session_id` lo genera la Pi o el servidor web?**
   - Recomendación: la Pi, formato `<timestamp>_<device_id>`. Facilita idempotencia sin consultar al servidor.

2. **¿El estado `created` se inserta antes o después de que la Pi empiece a capturar?**
   - Recomendación: antes. Así hay una fila desde el inicio y el servidor-web puede mostrar "Analizando" con certeza.

3. **¿Qué pasa si la Pi hace POST de una sesión que nunca se registró como `created`?**
   - El watcher la inserta directamente en estado `uploaded`. No es problema.

4. **¿La limpieza se ejecuta tras cada sesión o cada N?**
   - Tras cada sesión. Es una consulta barata.

5. **¿El navegador debe mostrar progreso detallado del análisis?**
   - Opcional. Para la demo, "Analizando..." con spinner es suficiente.

6. **¿Se implementa el panel de reprocesar con parámetros ajustables?**
   - Recomendación: sí, en fase posterior. El diseño lo permite.

7. **Cuando el usuario aborta, ¿se borra la fila de la sesión o se marca como `error`?**
   - Recomendación: marcar como `error` con `duration_sec` parcial. Conserva el registro del intento sin procesar datos incompletos.

---

## 16. Hoja de ruta de implementación

| Fase | Qué se construye | Cómo se valida |
|------|-----------------|----------------|
| 1 | Esquema de PostgreSQL | `psql` muestra las 5 tablas |
| 2 | WebSocket Pi ↔ servidor-web | Mover el dedo dibuja la onda en el navegador |
| 3 | Captura completa en la Pi | Se genera el JSONL al terminar |
| 4 | POST con checksum al watcher | El archivo aparece en `/data/raw/` |
| 5 | Watchdog + lanzamiento de Spark | Spark lee y escribe en PostgreSQL |
| 6 | Modelo de clasificación | `stress_windows` se puebla |
| 7 | Máquina de estados | La sesión transita por todos los estados |
| 8 | Pantalla única con 4 estados | Flujo completo sin intervención manual |
| 9 | Cancelación con `abort` | El usuario puede detener a mitad y volver a bienvenida |
| 10 | Limpieza de sesiones viejas | Con 11 sesiones, la más antigua desaparece |
| 11 | Compose completo | `compose up` levanta todo |

Cada fase es verificable por separado. No intentar hacer varias a la vez.

---

## 17. Conclusión

El sistema resuelve un problema acotado con una arquitectura clara:

- **Problema**: no existe una plataforma abierta que integre adquisición PPG, análisis HRV y clasificación de estrés con trazabilidad completa.
- **Alcance**: 5 minutos de captura, 4 estados de sesión, 10 sesiones retenidas, sin uso clínico.
- **Estrategia**: 3 contenedores permanentes + 2 efímeros, comunicación por WebSocket y HTTP, persistencia en PostgreSQL, máquina de estados explícita.

**La arquitectura prioriza simplicidad, explicabilidad y robustez sobre latencia mínima.** El diseño permite añadir streaming, colas o múltiples usuarios sin rehacer la base.

Cada componente se explica en una frase, cada decisión tiene justificación, cada falla tiene mitigación. Eso es lo que distingue un prototipo de un sistema de ingeniería.
# Registro de decisiones

## Arquitectura
- HTTP POST con checksum en lugar de NFS/Samba: confirmación a nivel de aplicación.
- Watchdog en lugar de polling: reacción en milisegundos, sin consumo en reposo.
- Compose (no Kubernetes): escala del proyecto no lo requiere.

## Comunicación
- WebSocket para la señal en vivo: bidireccional, baja latencia.
- HTTP POST para transferencia: confirmación explícita, reintentos acotados.
- Servidor web pasivo: no persiste, no coordina, solo muestra.

## Persistencia
- JSONL como crudo: nativo en Spark, inspeccionable a mano.
- PostgreSQL como resultados: consultas, transacciones, ON DELETE CASCADE.
- 10 sesiones retenidas: límite de la demo, limpieza automática.

## Análisis
- Análisis batch: el caso de uso no requiere tiempo real.
- Pulse-PPG preentrenado: no hay dataset etiquetado propio.
- Pulse-PPG da embeddings, no etiquetas: el encoder se usa congelado y encima va una regresión logística entrenada una vez sobre WESAD (dataset público de estrés). Es lo mismo que la evaluación "linear probe" del paper; no se reentrena la red.
- Ventanas de 30 s también al entrenar el clasificador (el paper usa 60 s): así `stress_windows` no cambia de esquema.
- PyTorch solo CPU en la imagen del modelo: una sesión son ~10 ventanas y tarda segundos; no hace falta GPU.
- Modelo en un contenedor propio, no dentro de la imagen de Spark: PyTorch no depende de la JVM y cada imagen se reconstruye por separado.
- Reintento único de Spark/modelo: dos fallas indican bug, no transitorio.

## Confiabilidad
- Máquina de estados explícita: elimina fallas silenciosas.
- Idempotencia por session_id: reintentos no duplican.
- Respaldo local en /tmp: la Pi conserva el crudo hasta siguiente sesión.
- Cancelación con abort: el usuario puede interrumpir sin dejar residuos.

## Pantalla (Fase 8)
- Una sola página con 4 estados (bienvenida, captura, analizando, resultados) que cambian sin recargar.
- El servidor-web sigue pasivo: solo lee PostgreSQL. La sesión la crea el watcher al recibir el POST; no se inserta en `created` al pulsar "Comenzar".
- La Pi incluye el `session_id` en cada `status`; el navegador lo usa para consultar `/api/estado` cada 2 s tras `done` (no hace falta `results_ready`).
- Estado global de la sesión según la fracción de ventanas clasificadas como estrés: menos de 1/3 bajo, menos de 2/3 moderado, si no alto.
- Línea de tiempo con HTML y CSS (una barra por ventana, altura = probabilidad), sin Chart.js: 10 ventanas no justifican una librería más.
- El pulso en vivo durante la captura es una estimación del navegador sobre los últimos 10 s; el que cuenta es el de Spark en los resultados.

## Cancelación (Fase 9)
- El abort lo registra el watcher (`POST /abort` desde la Pi), no el servidor-web: el servidor-web sigue sin escribir y el watcher sigue siendo el único que crea sesiones. La propuesta decía "servidor-web"; se cambió por esto.
- La sesión abortada se conserva en `error` con la duración parcial y `status_detail` "abort: ...", no se borra (pregunta abierta 7). Se crea en `created` y pasa a `error` en la misma transacción, así el historial muestra ambas transiciones sin tocar la máquina de estados.
- La Pi avisa al watcher antes de emitir `aborted`, con un solo intento de 3 s: la lista de bienvenida ya muestra la sesión como "cancelada" y el usuario no espera reintentos. Si falla, el aviso va a `pending/` y se reintenta como los uploads.

## Limpieza (Fase 10)
- La retención borra las sesiones fuera de las 10 más recientes que estén en `ready` o en `error`, no solo en `ready` como decía la propuesta: las cancelaciones quedan en `error` y si no se acumularían sin límite. Las que siguen en curso (`created`, `uploaded`, `processing`) nunca se borran, ni la que acaba de terminar aunque su `start_time` sea viejo (un reintento desde `pending/`): el navegador puede estar esperándola.
- El crudo se borra solo cuando la sesión queda en `ready`; en `error` se conserva para revisarlo y se va cuando la sesión sale de las 10 retenidas.
- La retención corre después de cada sesión que termina (análisis o `abort`) y al arrancar el watcher, no con un temporizador: solo cambia algo cuando llega una sesión.
- Al arrancar, antes de aceptar uploads, el watcher barre lo que un corte pudo dejar: temporales de `.incoming/`, crudo de sesiones ya listas y archivos sin sesión en la base.

## Compose completo (Fase 11)
- El simulador de la Pi sigue en el `compose up` por defecto: sin sensor es la única fuente de datos. Con la Pi real se levantan solo `postgres servidor-web watcher`.
- La prueba de punta a punta corre en su propio contenedor (perfil `e2e`), no en la máquina: la única dependencia sigue siendo Docker. Usa lo mismo que el navegador (Socket.IO y la API HTTP), no consulta PostgreSQL directo, así prueba también al servidor-web.
- `up --wait` funciona con spark y modelo: Compose acepta que un servicio de solo comprobación termine con código 0.

## Descartado
- Kafka: añade componente sin resolver problema real.
- Spark Streaming: latencia no es prioridad.
- Display 16x2: eliminado, todo el feedback va por pantalla.
- Botón físico: eliminado, todo el control va por navegador.
- Múltiples reintentos con backoff largo: 3 con backoff corto basta.
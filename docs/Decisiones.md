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

## Descartado
- Kafka: añade componente sin resolver problema real.
- Spark Streaming: latencia no es prioridad.
- Display 16x2: eliminado, todo el feedback va por pantalla.
- Botón físico: eliminado, todo el control va por navegador.
- Múltiples reintentos con backoff largo: 3 con backoff corto basta.
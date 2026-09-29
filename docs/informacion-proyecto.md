# Información del proyecto

## 1. Título del proyecto

**PulsePPG Stress Monitor: sistema de bajo costo para la adquisición de señal fotopletismográfica y la clasificación de estrés mediante análisis HRV y un modelo fundacional preentrenado**

## 2. Alcance

El proyecto desarrolla un prototipo funcional, de código abierto y reproducible, que cubre el flujo completo desde la captura de la señal hasta la presentación de resultados.

**Incluye:**

- **Adquisición:** sesión de 5 minutos de señal PPG (canales infrarrojo y rojo, 50 Hz) con un sensor MAX30102 conectado por I2C a una Raspberry Pi 5, con visualización de la onda en vivo.
- **Transferencia confiable:** envío del archivo crudo (JSONL) por HTTP con verificación de integridad SHA-256, hasta tres reintentos y respaldo local en la Pi.
- **Procesamiento:** detección de picos y cálculo de métricas de variabilidad de la frecuencia cardíaca (BPM, SDNN, RMSSD y pNN50) con Apache Spark.
- **Clasificación:** estimación de estrés en ventanas de 30 s con el encoder preentrenado Pulse-PPG y un clasificador lineal entrenado con el conjunto público WESAD (validación dejando sujetos fuera: F1 macro 0.822 y exactitud balanceada 0.844).
- **Persistencia:** PostgreSQL con máquina de estados por sesión, historial de eventos y retención de las 10 sesiones más recientes.
- **Presentación:** interfaz web de pantalla única (bienvenida, captura en vivo, análisis y resultados), con opción de cancelar la prueba.
- **Reproducibilidad:** el sistema completo se levanta con un solo `docker compose up` y cuenta con una prueba automática de punta a punta.

**No incluye:**

- Uso clínico ni diagnóstico médico.
- Validación con una población amplia ni calibración individual por línea base.
- Reentrenamiento del modelo fundacional (solo se entrena la capa de clasificación).
- Análisis en tiempo real estricto, multiusuario, autenticación ni despliegue en la nube.

**Estado actual:** las 11 fases de la hoja de ruta están implementadas y verificadas de punta a punta con un simulador de la Pi que genera una señal PPG sintética. Queda pendiente la lectura del sensor MAX30102 real en la Raspberry Pi 5 y su validación de calidad.

## 3. Descripción del problema

El estrés sostenido altera el sistema nervioso autónomo, y ese efecto se refleja en la variabilidad de la frecuencia cardíaca (HRV), que puede estimarse a partir de una señal PPG. Sin embargo, los métodos clínicos para medir el estrés suelen ser costosos, invasivos o poco accesibles fuera de un entorno especializado.

Las alternativas disponibles resuelven solo una parte del problema:

- **Bibliotecas de análisis** (HeartPy, NeuroKit2, pvPPG): suponen datos ya adquiridos y limpios, no gestionan el hardware ni ofrecen una interfaz final.
- **Aplicaciones móviles basadas en cámara (rPPG):** su calidad depende fuertemente de la iluminación y del movimiento.
- **Dispositivos comerciales:** funcionan como cajas negras; su firmware y sus algoritmos no son inspeccionables y los datos no se exportan en formatos abiertos.

En consecuencia, **no existe una plataforma abierta y de bajo costo, con un sensor de contacto dedicado, que integre en un mismo flujo reproducible la adquisición, el análisis HRV, la clasificación de estrés y la trazabilidad de cada etapa.**

## 4. Estrategia de solución

La solución se organiza como una arquitectura desacoplada en cinco etapas, cada una con su propio ciclo de vida y su propio contenedor, bajo el principio: *"La Pi captura y transfiere. El watcher recibe y coordina. Spark y el modelo analizan. PostgreSQL guarda. El servidor web muestra."*

1. **Adquisición (Raspberry Pi 5 + MAX30102):** captura la señal, la transmite en vivo por WebSocket, la escribe en un archivo JSONL y al terminar la envía al watcher con su suma SHA-256.
2. **Coordinación (watcher):** recibe y valida el archivo, detecta su llegada mediante un *watchdog* sobre la carpeta, lanza los contenedores de análisis, actualiza el estado de la sesión y limpia los datos antiguos.
3. **Análisis (Spark y modelo, contenedores efímeros):** Spark calcula picos y métricas HRV; después, el modelo Pulse-PPG genera representaciones de cada ventana de 30 s y el clasificador lineal asigna la etiqueta estrés/sin estrés con su probabilidad.
4. **Persistencia (PostgreSQL):** almacena sesiones, métricas, picos, ventanas de estrés e historial. Una máquina de estados aplicada en la base (created → uploaded → processing → ready, con salida a error) impide transiciones inválidas y permite retomar sesiones tras una falla.
5. **Presentación (servidor web Flask + Socket.IO):** muestra la captura en vivo y los resultados; solo consulta la base, sin escribir en ella.

**Criterios de diseño:**

- **Confiabilidad:** verificación de integridad, idempotencia por identificador de sesión, reintentos acotados y recuperación automática de sesiones pendientes al reiniciar.
- **Trazabilidad:** cada resultado puede rastrearse hasta la sesión y la muestra cruda que lo originó.
- **Desarrollo incremental:** el trabajo se dividió en 11 fases verificables de forma aislada. Mientras no se dispone del sensor, un simulador de la Pi permite validar el sistema completo.
- **Reproducibilidad:** todo el sistema se construye y levanta con un solo comando (`docker compose up`) en cualquier equipo que tenga Docker, y una prueba automática de punta a punta confirma su funcionamiento.

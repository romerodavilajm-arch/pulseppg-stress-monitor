# PulsePPG Stress Monitor

Sistema de adquisición de señal PPG con hardware de bajo costo (Raspberry Pi 5 + MAX30102) que calcula métricas HRV con Spark y clasifica niveles de estrés con el modelo Pulse-PPG preentrenado.

## Arquitectura

- **Adquisición**: Raspberry Pi 5 + MAX30102 (I2C, 50 Hz)
- **Coordinación**: Watcher con watchdog
- **Análisis**: Spark (HRV) + Pulse-PPG (clasificación)
- **Persistencia**: PostgreSQL
- **Presentación**: Dashboard Flask + Chart.js

## Levantar el sistema

Solo hace falta Docker con Compose v2:

```bash
docker compose up -d --build --wait
```

Abrir `http://localhost:5000`. Mientras no haya sensor, un simulador hace de
Pi. Prueba automática de punta a punta (con el sistema levantado):

```bash
docker compose --profile e2e run --rm e2e
```

Detalles, sesiones cortas y cómo usar la Pi real: [e2e/TESTING.md](e2e/TESTING.md).

## Documentación

- [Propuesta técnica](docs/Propuesta%20técnica.md)
- [Glosario](docs/Glosario.md)
- [Decisiones de diseño](docs/Decisiones.md)
- [Estado actual](docs/Estado%20actual.md)

## Estado

🚧 En desarrollo — Fase 11: el sistema completo se levanta con un solo `docker compose up` y se prueba de punta a punta (con simulador; falta la captura con el MAX30102). Ver [Estado actual](docs/Estado%20actual.md).

## Licencia

MIT

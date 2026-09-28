# PulsePPG Stress Monitor

Sistema de adquisición de señal PPG con hardware de bajo costo (Raspberry Pi 5 + MAX30102) que calcula métricas HRV con Spark y clasifica niveles de estrés con el modelo Pulse-PPG preentrenado.

## Arquitectura

- **Adquisición**: Raspberry Pi 5 + MAX30102 (I2C, 50 Hz)
- **Coordinación**: Watcher con watchdog
- **Análisis**: Spark (HRV) + Pulse-PPG (clasificación)
- **Persistencia**: PostgreSQL
- **Presentación**: Dashboard Flask + Chart.js

## Documentación

- [Propuesta técnica](docs/Propuesta%20técnica.md)
- [Glosario](docs/Glosario.md)
- [Decisiones de diseño](docs/Decisiones.md)
- [Estado actual](docs/Estado%20actual.md)

## Estado

🚧 En desarrollo — Fase 4: transferencia al watcher (con simulador). Ver [Estado actual](docs/Estado%20actual.md).

## Licencia

MIT

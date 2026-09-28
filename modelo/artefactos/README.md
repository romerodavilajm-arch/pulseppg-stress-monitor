# Artefactos del modelo

Aquí vive el clasificador de estrés que usa `infer.py`:

- `clasificador.joblib`: StandardScaler + regresión logística sobre los
  embeddings de Pulse-PPG, entrenado sobre WESAD con `train.py`.
- `clasificador.json`: sus metadatos legibles (métrica por sujeto, ventana,
  sha256 de los pesos del encoder, fecha).

Se entrena una sola vez y se versiona en el repositorio (pesa unos KB), así
que `docker compose up` en una máquina limpia ya lo trae. Cómo entrenarlo:
`modelo/TESTING.md`, sección 1.

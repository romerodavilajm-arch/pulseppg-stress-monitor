# Glosario

- **PPG (Fotopletismografía)**: Técnica óptica que mide cambios en el volumen 
  sanguíneo. Base del MAX30102.

- **HRV (Variabilidad de Frecuencia Cardíaca)**: Variación en los intervalos 
  entre latidos consecutivos. Indicador del sistema nervioso autónomo.

- **SDNN**: Desviación estándar de los intervalos RR. Indicador global de HRV.

- **RMSSD**: Raíz cuadrada de la media de las diferencias al cuadrado entre 
  intervalos RR sucesivos. Indicador de actividad parasimpática.

- **pNN50**: Porcentaje de intervalos RR que difieren más de 50 ms del anterior.

- **BPM**: Latidos por minuto.

- **IR / Red**: Canales del MAX30102 (infrarrojo y rojo). El IR se usa para 
  detectar el pulso; el Red para cálculo de SpO2 (no usado en este proyecto).

- **Ventana de análisis**: Segmento temporal sobre el que se calcula una métrica. 
  Para HRV, 30 s por ventana. Para clasificación de estrés, 30 s por ventana.

- **Sesión**: Una captura completa de 5 minutos con todos sus datos asociados.

- **Máquina de estados**: Modelo que define los estados válidos de una sesión 
  y las transiciones permitidas entre ellos.

- **Idempotencia**: Propiedad que garantiza que una operación repetida no 
  produce efectos adicionales. Aplicada al POST de la Pi al watcher.
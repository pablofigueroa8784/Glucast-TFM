# Glucast — Servidor de alarmas

Servicio Flask que lee Nightscout, predice la glucosa a 30 minutos con el LSTM unificado del TFM
(calculado con `numpy`, sin TensorFlow) y devuelve el estado de alarma:

| Estado | Regla | Mensaje en la app |
|---|---|---|
| `hipo` | límite inferior conformal (α = 0,20) < 70 mg/dL | texto en rojo |
| `hiper` | predicción puntual > 195 mg/dL | texto en negrita |
| `ok` | ninguna | sin aviso |
| `sin_datos` | hueco del sensor en las últimas 2 h o lectura con más de 20 min | sin aviso |

## Endpoints
- `GET /health` — estado del servicio.
- `GET /alerta` — requiere la cabecera `X-Glucast-Key: <ALERTA_KEY>`.

## Variables de entorno (Railway → Variables)
- `NIGHTSCOUT_URL`, `NIGHTSCOUT_TOKEN` (token de **lectura**), `ALERTA_KEY` (clave compartida con la app).

## Archivos del modelo
`glucast_lstm_30min.npz` y `glucast_alarmas.json` se generan con
`notebooks/Glucast_Exportar_Modelo_Servidor.ipynb`, que verifica que el cálculo en numpy coincide con Keras.
La comparación sobre 384 instantes reales del período de prueba dio una diferencia máxima de 0,5 mg/dL (redondeo).

Glucast complementa las alertas del sensor y de SmartGuard; no las reemplaza ni es un dispositivo médico certificado.

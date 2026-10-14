# Glucast — Servidor de predicción

Servidor Flask que expone la predicción del modelo LSTM ajustado (OhioT1DM + datos propios) a 30 minutos.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado del servidor |
| GET | `/predecir` | Predice la glucemia 30 min después de la última lectura en Nightscout |
| GET | `/predecir?hasta=<ms>` | Igual, pero usando solo las lecturas anteriores a ese instante (útil para revisar días pasados) |

Respuesta de ejemplo:

```json
{
  "prediccion_mgdl": 124,
  "horizonte_min": 30,
  "glucosa_base": 140,
  "timestamp_base": 1791990523613,
  "timestamp_prediccion": 1791992323613,
  "minutos_desde_ultima_lectura": 0
}
```

Si faltan lecturas (se necesitan 2 h continuas de CGM) responde `422` con un mensaje en `error`.

## Cómo funciona

1. Lee de Nightscout las lecturas de las últimas 4 h y los bolos importados desde CareLink (`enteredBy = Glucast-CareLinkImport`).
2. Reconstruye las 12 variables con el mismo código del notebook `Glucast_OhioT1DM_Preentrenamiento.ipynb` (remuestreo a 5 min, IOB con DIA de 2 h, tendencia, hora y día cíclicos, fenómeno del alba). Las horas se pasan a la zona horaria local, como en el CSV de CareLink.
3. `carbs`, `fc` y `pasos` se fijan en 0 porque valieron 0 en todo el dataset de ajuste fino (el scaler las dejó en ese valor).
4. Escala la ventana de 24 lecturas con `glucast_finetune_scaler.pkl`, predice con `glucast_modelo_finetune.keras` y desescala a mg/dL.

## Archivos del modelo

Copiar a `modelo/` los archivos generados por el notebook (están en Drive, `TFM/GRAFICOS/EVAL 2DO MODELO`):

- `glucast_modelo_finetune.keras`
- `glucast_finetune_scaler.pkl`

No se suben al repositorio (ver `.gitignore`).

## Uso local

```bash
pip install -r requirements.txt
export NIGHTSCOUT_URL=https://tu-instancia-nightscout.example.com
python3 server.py        # escucha en el puerto 5001
curl localhost:5001/predecir
```

Variables de entorno: `NIGHTSCOUT_URL` (obligatoria), `NIGHTSCOUT_TOKEN` (si Nightscout no es de lectura pública), `MODELO_DIR`, `ZONA_HORARIA` (por defecto `America/Argentina/Buenos_Aires`).

## Deploy en Railway

Crear un servicio nuevo con raíz en `prediction-server/`; Railway detecta el `Procfile`. Configurar `NIGHTSCOUT_URL` y, si hace falta, `NIGHTSCOUT_TOKEN`. Luego poner la URL del servicio en `PREDICTION_SERVER_URL` de `config.js` en la app.

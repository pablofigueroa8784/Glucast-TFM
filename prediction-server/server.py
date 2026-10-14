"""
Glucast — Prediction Server
===========================
Servidor Flask que predice la glucemia a 30 minutos con el modelo LSTM
ajustado (OhioT1DM + datos propios).

Lee de Nightscout las últimas lecturas del CGM y los bolos importados desde
CareLink, reconstruye las 12 variables con el mismo feature engineering del
notebook `Glucast_OhioT1DM_Preentrenamiento.ipynb` (sección 10) y devuelve
la predicción en mg/dL.

Uso local:
    pip install -r requirements.txt
    export NIGHTSCOUT_URL=https://tu-instancia-nightscout.example.com
    python3 server.py

Variables de entorno:
    NIGHTSCOUT_URL     URL de Nightscout (obligatoria)
    NIGHTSCOUT_TOKEN   Token de lectura (opcional, si Nightscout no es público)
    MODELO_DIR         Carpeta con el modelo y el scaler (por defecto ./modelo)
    ZONA_HORARIA       Zona horaria de los datos (por defecto America/Argentina/Buenos_Aires)

Archivos esperados en MODELO_DIR (generados por el notebook):
    glucast_modelo_finetune.keras
    glucast_finetune_scaler.pkl
"""

import os
import pickle
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.after_request
def agregar_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return resp

# ── Configuración ──────────────────────────────────────────────────────────────

NIGHTSCOUT_URL = os.environ.get('NIGHTSCOUT_URL', '').rstrip('/')
NIGHTSCOUT_TOKEN = os.environ.get('NIGHTSCOUT_TOKEN', '')
MODELO_DIR = os.environ.get('MODELO_DIR', os.path.join(os.path.dirname(__file__), 'modelo'))
ZONA_HORARIA = os.environ.get('ZONA_HORARIA', 'America/Argentina/Buenos_Aires')

# Deben coincidir con el notebook de entrenamiento
FEATURES = ['glucosa', 'bolo_u', 'iob', 'tendencia', 'carbs',
            'hora_sin', 'hora_cos', 'dow_sin', 'dow_cos', 'alba', 'fc', 'pasos']
TARGET = 'glucosa'
VENTANA = 24        # 2 horas de historia (24 × 5 min)
HORIZONTE = 6       # 30 minutos (6 × 5 min)
DIA = 24            # Duración de acción de la insulina: 2 h (Fiasp)

# Bolos importados desde CareLink: misma fuente que en el entrenamiento
ORIGEN_BOLOS = 'Glucast-CareLinkImport'

_modelo = None
_scaler = None


def cargar_modelo():
    """Carga el modelo y el scaler una sola vez (la primera predicción)."""
    global _modelo, _scaler
    if _modelo is None:
        import keras
        _modelo = keras.models.load_model(os.path.join(MODELO_DIR, 'glucast_modelo_finetune.keras'))
        with open(os.path.join(MODELO_DIR, 'glucast_finetune_scaler.pkl'), 'rb') as f:
            _scaler = pickle.load(f)
    return _modelo, _scaler

# ── Nightscout ─────────────────────────────────────────────────────────────────


def _get_nightscout(ruta, params):
    if NIGHTSCOUT_TOKEN:
        params = {**params, 'token': NIGHTSCOUT_TOKEN}
    resp = requests.get(f'{NIGHTSCOUT_URL}{ruta}', params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def leer_glucosa(hasta_ms):
    """Lecturas de CGM de las 4 h previas a `hasta_ms` (2 h de ventana + 2 h de IOB)."""
    desde_ms = hasta_ms - 4 * 3600 * 1000
    datos = _get_nightscout('/api/v1/entries.json', {
        'find[date][$gte]': desde_ms,
        'find[date][$lte]': hasta_ms,
        'count': 200,
    })
    return [(e['date'], e['sgv']) for e in datos if e.get('sgv')]


def leer_bolos(hasta_ms):
    """Bolos de las 4 h previas a `hasta_ms`, importados desde CareLink."""
    desde = datetime.fromtimestamp((hasta_ms - 4 * 3600 * 1000) / 1000, tz=timezone.utc)
    hasta = datetime.fromtimestamp(hasta_ms / 1000, tz=timezone.utc)
    datos = _get_nightscout('/api/v1/treatments.json', {
        'find[enteredBy]': ORIGEN_BOLOS,
        'find[created_at][$gte]': desde.isoformat(),
        'find[created_at][$lte]': hasta.isoformat(),
        'count': 100,
    })
    return [(t['created_at'], float(t['insulin'])) for t in datos if t.get('insulin')]

# ── Feature engineering (igual que el notebook) ────────────────────────────────


def construir_features(lecturas, bolos):
    """Devuelve el DataFrame en resolución de 5 min con las 12 variables del modelo.

    Las marcas de tiempo se pasan a hora local sin zona, como en el CSV de
    CareLink usado para entrenar, para que hora, día y alba coincidan.
    """
    df_gluc = pd.DataFrame(lecturas, columns=['timestamp', 'glucosa'])
    df_gluc['timestamp'] = (pd.to_datetime(df_gluc['timestamp'], unit='ms', utc=True)
                            .dt.tz_convert(ZONA_HORARIA).dt.tz_localize(None))
    df_gluc = df_gluc.set_index('timestamp').sort_index()

    df_base = df_gluc.resample('5min').mean().interpolate(method='linear', limit=3)

    if bolos:
        df_bolos = pd.DataFrame(bolos, columns=['timestamp', 'bolo_u'])
        df_bolos['timestamp'] = (pd.to_datetime(df_bolos['timestamp'], utc=True)
                                 .dt.tz_convert(ZONA_HORARIA).dt.tz_localize(None))
        df_bolos = df_bolos.set_index('timestamp').sort_index()
        df_base = df_base.join(df_bolos.resample('5min').sum(), how='left')
    df_base['bolo_u'] = df_base.get('bolo_u', pd.Series(0.0, index=df_base.index)).fillna(0)

    pesos_iob = np.array([max(0, 1 - i / DIA) for i in range(DIA)])
    df_base['iob'] = df_base['bolo_u'].rolling(DIA, min_periods=1).apply(
        lambda x: np.sum(x[::-1] * pesos_iob[:len(x)]), raw=True)
    df_base['tendencia'] = df_base['glucosa'].diff().fillna(0)

    hora = df_base.index.hour + df_base.index.minute / 60
    df_base['hora_sin'] = np.sin(2 * np.pi * hora / 24)
    df_base['hora_cos'] = np.cos(2 * np.pi * hora / 24)
    dow = df_base.index.dayofweek
    df_base['dow_sin'] = np.sin(2 * np.pi * dow / 7)
    df_base['dow_cos'] = np.cos(2 * np.pi * dow / 7)
    df_base['alba'] = ((df_base.index.hour >= 5) & (df_base.index.hour < 8)).astype(int)

    # En el ajuste fino estas variables valieron 0 en todo el dataset propio
    # (CareLink no trae carbohidratos y la FC/pasos diarios de Garmin no llegaron
    # al merge), así que el scaler las fijó en 0. Se replica para no sacar al
    # modelo de su distribución de entrenamiento.
    df_base['carbs'] = 0.0
    df_base['fc'] = 0.0
    df_base['pasos'] = 0.0

    return df_base


def predecir(hasta_ms):
    lecturas = leer_glucosa(hasta_ms)
    if not lecturas:
        return None, 'No hay lecturas de glucosa en las 4 horas previas.'

    df = construir_features(lecturas, leer_bolos(hasta_ms))
    ventana = df[FEATURES].iloc[-VENTANA:]
    if len(ventana) < VENTANA or ventana['glucosa'].isna().any():
        return None, 'Faltan lecturas: se necesitan 2 horas continuas de CGM (huecos de hasta 15 min).'

    modelo, scaler = cargar_modelo()
    x = scaler.transform(ventana)[np.newaxis, :, :]
    y_sc = float(modelo.predict(x, verbose=0)[0, 0])

    idx = FEATURES.index(TARGET)
    prediccion = y_sc * scaler.data_range_[idx] + scaler.data_min_[idx]

    ts_ultima_ms, glucosa_ultima = max(lecturas)
    return {
        'prediccion_mgdl': round(prediccion),
        'horizonte_min': HORIZONTE * 5,
        'timestamp_base': int(ts_ultima_ms),
        'timestamp_prediccion': int(ts_ultima_ms + HORIZONTE * 5 * 60 * 1000),
        'glucosa_base': glucosa_ultima,
        'minutos_desde_ultima_lectura': round((datetime.now(timezone.utc).timestamp() * 1000 - ts_ultima_ms) / 60000),
    }, None

# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'nightscout_configurado': bool(NIGHTSCOUT_URL)})


@app.route('/predecir', methods=['GET'])
def endpoint_predecir():
    """Predice la glucemia 30 min después de la última lectura.

    Parámetro opcional `hasta` (ms epoch): usa las lecturas anteriores a ese
    instante, para ver qué habría predicho el modelo en un momento pasado.
    """
    if not NIGHTSCOUT_URL:
        return jsonify({'error': 'NIGHTSCOUT_URL no está configurada en el servidor.'}), 500
    try:
        hasta_ms = int(request.args.get('hasta') or datetime.now(timezone.utc).timestamp() * 1000)
    except ValueError:
        return jsonify({'error': 'El parámetro "hasta" debe ser un timestamp en milisegundos.'}), 400

    try:
        resultado, error = predecir(hasta_ms)
    except requests.RequestException as e:
        return jsonify({'error': f'No se pudo leer Nightscout: {e}'}), 502
    if error:
        return jsonify({'error': error}), 422
    return jsonify(resultado)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))

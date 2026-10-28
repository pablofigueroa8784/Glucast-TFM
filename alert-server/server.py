"""
Glucast — Servidor de alarmas (hipo e hiperglucemia)
====================================================
Lee las últimas horas de glucosa y tratamientos de Nightscout, arma las mismas 8 variables
que se usaron para entrenar el LSTM unificado, predice la glucosa a 30 minutos y aplica las
reglas de alarma del TFM (capítulos 6.3 y 6.4):

  - Hipoglucemia: límite inferior conformal (α = 0,20) < 70 mg/dL
  - Hiperglucemia: predicción puntual > 195 mg/dL

El LSTM se calcula con numpy (sin TensorFlow) a partir de los pesos exportados por
Glucast_Exportar_Modelo_Servidor.ipynb; ese notebook verifica que el resultado coincide con Keras.

Variables de entorno (Railway → Variables):
  NIGHTSCOUT_URL    https://<tu-nightscout>.up.railway.app
  NIGHTSCOUT_TOKEN  token de LECTURA de Nightscout (no el API secret)
  ALERTA_KEY        clave compartida con la app (cabecera X-Glucast-Key); evita que cualquiera lea tus datos

Glucast complementa las alertas del sensor y de SmartGuard; no las reemplaza ni es un dispositivo médico certificado.
"""
from zoneinfo import ZoneInfo
import os, json
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd, requests
from flask import Flask, request, jsonify

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, 'glucast_alarmas.json'), encoding='utf-8'))
PESOS = dict(np.load(os.path.join(BASE, 'glucast_lstm_30min.npz')))
NS_URL = os.environ.get('NIGHTSCOUT_URL', '').strip().rstrip('/')
NS_TOKEN = os.environ.get('NIGHTSCOUT_TOKEN', '')
ALERTA_KEY = os.environ.get('ALERTA_KEY', '').strip().strip('"').strip("'").strip()

app = Flask(__name__)

@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Glucast-Key'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return resp

# ---------------------------------------------------------------- modelo (numpy)
def _sig(x): return 1 / (1 + np.exp(-x))

def _lstm(x, W, U, b, secuencias):
    n, T, _ = x.shape; u = U.shape[0]; h = np.zeros((n, u)); c = np.zeros((n, u)); out = []
    for t in range(T):
        z = x[:, t, :] @ W + h @ U + b
        i, f, g, o = _sig(z[:, :u]), _sig(z[:, u:2*u]), np.tanh(z[:, 2*u:3*u]), _sig(z[:, 3*u:])
        c = f * c + i * g; h = o * np.tanh(c); out.append(h)
    return np.stack(out, 1) if secuencias else h

def predecir_delta(x):
    for cp in CFG['capas']:
        k = cp['id']
        if cp['tipo'] == 'lstm':
            x = _lstm(x, PESOS[f'l{k}_W'], PESOS[f'l{k}_U'], PESOS[f'l{k}_b'], cp['secuencias'])
        elif cp['tipo'] == 'bn':
            x = PESOS[f'l{k}_gamma'] * (x - PESOS[f'l{k}_media']) / np.sqrt(PESOS[f'l{k}_var'] + cp['eps']) + PESOS[f'l{k}_beta']
        else:
            x = x @ PESOS[f'l{k}_W'] + PESOS[f'l{k}_b']
            if cp['activacion'] == 'relu': x = np.maximum(x, 0)
    return x.ravel()

# ---------------------------------------------------------------- variables (idéntico al entrenamiento)
def construir_base(gluc, bolos, carbs):
    g = gluc.groupby(level=0).mean().resample('5min').mean()
    g = g.interpolate(method='linear', limit=3, limit_area='inside')   # huecos de hasta 15 min
    df = pd.DataFrame({'glucosa': g})
    df['bolo_u'] = bolos.resample('5min').sum().reindex(df.index).fillna(0) if len(bolos) else 0.0
    df['carbs'] = carbs.resample('5min').sum().reindex(df.index).fillna(0) if len(carbs) else 0.0
    dia, absc = CFG['dia_periodos'], CFG['absorcion_carbs']
    df['iob'] = np.convolve(df['bolo_u'].values, np.maximum(0, 1 - np.arange(dia) / dia))[:len(df)]
    df['cob'] = np.convolve(df['carbs'].values, np.maximum(0, 1 - np.arange(absc) / absc))[:len(df)]
    df['tendencia'] = df['glucosa'].diff()
    hora = df.index.hour + df.index.minute / 60
    df['hora_sin'] = np.sin(2 * np.pi * hora / 24); df['hora_cos'] = np.cos(2 * np.pi * hora / 24)
    return df

def a_local(ts): return ts.dt.tz_convert(CFG['zona_horaria']).dt.tz_localize(None)

def evaluar(df_sgv, df_trt, ahora=None):
    """df_sgv: columnas ts (UTC), glucosa. df_trt: ts (UTC), bolo_u, carbs. Devuelve el dict de la alerta."""
    s_g = pd.Series(df_sgv['glucosa'].astype(float).values, index=a_local(df_sgv['ts'])).sort_index()
    s_g = s_g[(s_g >= 39) & (s_g <= 401)]
    if len(df_trt):
        tl = df_trt.assign(ts=a_local(df_trt['ts'])).set_index('ts').sort_index()
        s_b, s_c = tl['bolo_u'][tl['bolo_u'] > 0], tl['carbs'][tl['carbs'] > 0]
    else:
        s_b = s_c = pd.Series(dtype=float)
    if len(s_g) < 10:
        return {'estado': 'sin_datos', 'mensaje': 'No hay suficientes lecturas recientes del sensor.'}
    df = construir_base(s_g, s_b, s_c)
    V = CFG['ventana']
    ult = df['glucosa'].last_valid_index(); df = df.loc[:ult]
    if len(df) < V + 1 or df['glucosa'].iloc[-(V + 1):].isna().any():
        return {'estado': 'sin_datos', 'mensaje': 'Hay un hueco del sensor en las últimas 2 horas; no se puede predecir.'}
    ahora = ahora if ahora is not None else pd.Timestamp.now(tz=CFG['zona_horaria']).tz_localize(None)
    edad_min = (ahora - ult).total_seconds() / 60
    if edad_min > 20:
        return {'estado': 'sin_datos', 'mensaje': f'La última lectura tiene {edad_min:.0f} minutos.'}
    ventana = df.iloc[-V:]
    X = np.stack([(ventana[c].fillna(0).values / CFG['escalas'][c]) for c in CFG['features']], axis=1)[None].astype('float64')
    g_act = float(ventana['glucosa'].iloc[-1])
    pred = g_act + float(predecir_delta(X)[0]) * CFG['escala_delta']
    Q, A = CFG['cuantiles_conformal'], CFG['alarmas']
    li = pred + Q[A['hipo']['cuantil']]; ls = pred + Q[A['hiper_temprano']['cuantil']]
    if li < A['hipo']['umbral']:
        estado, msj = 'hipo', A['hipo']['mensaje']
    elif pred > A['hiper']['umbral']:
        estado, msj = 'hiper', A['hiper']['mensaje']
    else:
        estado, msj = 'ok', ''
    return {'estado': estado, 'mensaje': msj, 'glucosa_actual': round(g_act), 'hora_lectura': ult.strftime('%Y-%m-%d %H:%M'),
            'prediccion_30min': round(pred), 'limite_inferior': round(li), 'limite_superior': round(ls),
            'modelo': CFG['version'], 'aviso': CFG['aviso_legal']}

# ---------------------------------------------------------------- Nightscout
def diagnostico_nightscout():
    """Cuándo fue la última lectura que Nightscout tiene guardada (sin exponer datos sensibles)."""
    try:
        r = requests.get(f'{NS_URL}/api/v1/entries/sgv.json',
                         params={'token': NS_TOKEN, 'count': 1, 'find[date][$gte]': 0}, timeout=20)
        d = {'http_nightscout': r.status_code, 'ultima_lectura': None}
        j = r.json() if r.ok else None
        if isinstance(j, list) and j and j[0].get('date'):
            ult = datetime.fromtimestamp(j[0]['date'] / 1000, tz=timezone.utc).astimezone(ZoneInfo(CFG['zona_horaria']))
            d['ultima_lectura'] = ult.strftime('%Y-%m-%d %H:%M')
            d['horas_desde_ultima'] = round((datetime.now(timezone.utc) - ult).total_seconds() / 3600, 1)
        return d
    except Exception as ex:
        return {'error': type(ex).__name__}

def leer_nightscout(hasta=None, horas=6, extra_min=0):
    """Lecturas y tratamientos entre (hasta - horas) y (hasta + extra_min). hasta=None -> ahora."""
    hasta = hasta or datetime.now(timezone.utc)
    desde, fin = hasta - timedelta(hours=horas), hasta + timedelta(minutes=extra_min)
    p = {'token': NS_TOKEN, 'count': 1000}
    e = requests.get(f'{NS_URL}/api/v1/entries/sgv.json', params={**p, 'find[date][$gte]': int(desde.timestamp() * 1000),
                     'find[date][$lte]': int(fin.timestamp() * 1000)}, timeout=20).json()
    t = requests.get(f'{NS_URL}/api/v1/treatments.json', params={**p, 'find[created_at][$gte]': desde.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                     'find[created_at][$lte]': fin.strftime('%Y-%m-%dT%H:%M:%S.000Z')}, timeout=20).json()
    df_sgv = pd.DataFrame([{'ts': x['date'], 'glucosa': x['sgv']} for x in e if x.get('sgv') and x.get('date')])
    df_sgv['ts'] = pd.to_datetime(df_sgv['ts'], unit='ms', utc=True) if len(df_sgv) else pd.Series(dtype='datetime64[ns, UTC]')
    filas = [{'ts': x['created_at'], 'bolo_u': float(x.get('insulin') or 0), 'carbs': float(x.get('carbs') or 0)} for x in t if x.get('created_at')]
    df_trt = pd.DataFrame(filas, columns=['ts', 'bolo_u', 'carbs'])
    if len(df_trt): df_trt['ts'] = pd.to_datetime(df_trt['ts'], utc=True, format='ISO8601')
    return df_sgv, df_trt

@app.route('/health')
def health():
    return jsonify({'service': 'Glucast Alert Server', 'status': 'ok', 'modelo': CFG['version'], 'nightscout_configurado': bool(NS_URL and NS_TOKEN),
                    'clave_configurada': bool(ALERTA_KEY), 'largo_clave': len(ALERTA_KEY)})

@app.route('/alerta')
def alerta():
    if ALERTA_KEY and (request.headers.get('X-Glucast-Key') or '').strip() != ALERTA_KEY:
        return jsonify({'error': 'no autorizado'}), 401
    simular = (request.args.get('simular') or '').strip()
    try:
        if simular:
            return jsonify(simular_alerta(simular))
        df_sgv, df_trt = leer_nightscout()
        if df_sgv.empty:
            return jsonify({'estado': 'sin_datos', 'mensaje': 'Nightscout no tiene lecturas de las últimas horas (no hay datos en vivo).',
                            'diagnostico': diagnostico_nightscout()})
        return jsonify(evaluar(df_sgv, df_trt))
    except ValueError as ex:
        return jsonify({'estado': 'error', 'mensaje': str(ex)}), 400
    except Exception as ex:
        return jsonify({'estado': 'error', 'mensaje': f'No se pudo calcular la alerta: {type(ex).__name__}'}), 502

def simular_alerta(texto):
    """Modo simulación: qué habría avisado Glucast en un momento pasado (hora local, AAAA-MM-DD HH:MM),
    usando solo los datos disponibles hasta ese momento. Agrega la glucosa real 30 min después para comparar."""
    try:
        t_loc = pd.Timestamp(texto.replace('T', ' '))
    except Exception:
        raise ValueError('Formato de fecha inválido. Usá AAAA-MM-DD HH:MM (hora de Argentina).')
    if t_loc.tzinfo is not None:
        t_loc = t_loc.tz_convert(CFG['zona_horaria']).tz_localize(None)
    t_utc = t_loc.tz_localize(CFG['zona_horaria']).tz_convert('UTC').to_pydatetime()
    df_sgv, df_trt = leer_nightscout(hasta=t_utc, extra_min=40)
    base = {'simulacion': True, 'momento_simulado': t_loc.strftime('%Y-%m-%d %H:%M')}
    if df_sgv.empty:
        return {**base, 'estado': 'sin_datos', 'mensaje': 'Nightscout no tiene lecturas en ese momento.'}
    t_ts = pd.Timestamp(t_utc)
    pasado = df_sgv[df_sgv['ts'] <= t_ts]
    trt_pasado = df_trt[df_trt['ts'] <= t_ts] if len(df_trt) else df_trt
    if pasado.empty:
        return {**base, 'estado': 'sin_datos', 'mensaje': 'No hay lecturas antes de ese momento.'}
    res = evaluar(pasado, trt_pasado, ahora=t_loc)
    futuro = df_sgv[(df_sgv['ts'] > t_ts + pd.Timedelta(minutes=25)) & (df_sgv['ts'] <= t_ts + pd.Timedelta(minutes=35))]
    if len(futuro):
        k = (futuro['ts'] - (t_ts + pd.Timedelta(minutes=30))).abs().idxmin()
        res['glucosa_real_30min'] = int(futuro.loc[k, 'glucosa'])
    return {**base, **res}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5002)))

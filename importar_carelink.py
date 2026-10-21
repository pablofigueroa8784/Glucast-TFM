"""
Glucast — Importador de exportación CSV de CareLink a Nightscout
================================================================
Lee el CSV que se descarga desde CareLink (Informes > Exportar datos / "Export CSV")
y sube a Nightscout:
  - Glucosa del sensor  -> entries (type "sgv")
  - Bolos administrados -> treatments ("Correction Bolus" / "Meal Bolus")
  - Carbohidratos (BWZ) -> treatments ("Carb Correction" / "Meal Bolus")
  - Glucemias capilares -> treatments ("BG Check")

Es idempotente: antes de subir consulta lo que ya existe en Nightscout en ese
rango de fechas y omite duplicados, así que se puede correr varias veces.

Uso (Windows PowerShell):
    $env:NIGHTSCOUT_URL = "https://<tu-nightscout>.up.railway.app"
    $env:NIGHTSCOUT_API_SECRET = "<api secret en texto plano>"
    python importar_carelink.py "C:\\ruta\\CareLink-Export.csv" --dry-run   # solo muestra
    python importar_carelink.py "C:\\ruta\\CareLink-Export.csv"             # sube

Uso (Mac / Linux):
    export NIGHTSCOUT_URL=...  NIGHTSCOUT_API_SECRET=...
    python3 importar_carelink.py ~/Downloads/CareLink-Export.csv --dry-run

Solo usa la biblioteca estándar de Python (3.8+).
"""

import argparse
import bisect
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEVICE = 'carelink-csv'
LOTE = 500

# Palabras clave (inglés y español) para reconocer las columnas del CSV de CareLink
COLUMNAS = {
    'fecha': [['date'], ['fecha']],
    'hora': [['time'], ['hora']],
    'sgv': [['sensor glucose'], ['glucosa', 'sensor']],
    'bg': [['bg reading'], ['lectura', 'gs'], ['lectura', 'glucosa'], ['glucemia']],
    'bolo': [['bolus volume delivered'], ['bolo', 'administrado'], ['bolo', 'suministrado'],
             ['volumen', 'bolo', 'entregado']],
    'carbs': [['bwz carb input'], ['carb input'], ['carbohidrat'], ['hidratos']],
}


def normalizar(txt):
    txt = unicodedata.normalize('NFKD', txt or '').encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', txt.strip().lower())


def hash_secret(secret):
    return hashlib.sha1(secret.encode()).hexdigest()


def leer_texto(ruta):
    with open(ruta, 'rb') as f:
        crudo = f.read()
    for cod in ('utf-8-sig', 'utf-16', 'latin-1'):
        try:
            txt = crudo.decode(cod)
            if cod == 'utf-16' and '\x00' in txt:
                continue
            return txt
        except UnicodeDecodeError:
            continue
    return crudo.decode('latin-1', errors='replace')


def es_encabezado(celdas):
    n = [normalizar(c) for c in celdas]
    tiene_fecha = any(c in ('date', 'fecha') for c in n)
    tiene_hora = any(c in ('time', 'hora') for c in n)
    return tiene_fecha and tiene_hora


def mapear_columnas(encabezado):
    n = [normalizar(c) for c in encabezado]
    mapa = {}
    for clave, alternativas in COLUMNAS.items():
        for i, col in enumerate(n):
            if clave in ('fecha', 'hora'):
                if col in [a[0] for a in alternativas]:
                    mapa[clave] = i
                    break
            elif any(all(p in col for p in alt) for alt in alternativas):
                # evitar confundir "BG Reading" con columnas de calibración o "BWZ BG Input"
                if clave == 'bg' and ('calibra' in col or 'bwz' in col or 'asistente' in col):
                    continue
                mapa[clave] = i
                break
    return mapa


def numero(txt):
    txt = (txt or '').strip().replace('"', '')
    if not txt:
        return None
    if ',' in txt and '.' not in txt:
        txt = txt.replace(',', '.')
    try:
        return float(txt)
    except ValueError:
        return None


FORMATOS_FECHA = ['%Y/%m/%d', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%y', '%y/%m/%d']
FORMATOS_HORA = ['%H:%M:%S', '%H:%M']


def parsear_fecha(fecha, hora, tz, formato_cache):
    fecha, hora = (fecha or '').strip(), (hora or '').strip()
    if not fecha or not hora:
        return None
    candidatos = ([formato_cache[0]] if formato_cache else []) + FORMATOS_FECHA
    for ff in candidatos:
        for fh in FORMATOS_HORA:
            try:
                dt = datetime.strptime(f'{fecha} {hora}', f'{ff} {fh}')
                if not formato_cache:
                    formato_cache.append(ff)
                return dt.replace(tzinfo=tz)
            except ValueError:
                continue
    return None


def leer_carelink(ruta, tz):
    texto = leer_texto(ruta)
    primera = next((l for l in texto.splitlines() if l.strip()), '')
    muestra = '\n'.join(texto.splitlines()[:60])
    delim = ';' if muestra.count(';') > muestra.count(',') else ','
    lector = csv.reader(io.StringIO(texto), delimiter=delim)

    sgv, bg, bolos, carbs = {}, {}, {}, {}
    mapa, columnas_vistas, formato_cache, saltar = None, set(), [], False
    for celdas in lector:
        if not celdas or not any(c.strip() for c in celdas):
            continue
        if celdas[0].strip().startswith('---'):
            # Separador de sección. "Aggregated Auto Insulin Data" son TOTALES DIARIOS de
            # insulina automática: no son bolos reales y se omiten.
            saltar = 'aggregated' in normalizar(' '.join(celdas))
            mapa = None
            continue
        if saltar:
            continue
        if es_encabezado(celdas):
            mapa = mapear_columnas(celdas)
            columnas_vistas.update(k for k in mapa)
            continue
        if not mapa or 'fecha' not in mapa or 'hora' not in mapa:
            continue

        def celda(clave):
            i = mapa.get(clave)
            return celdas[i] if i is not None and i < len(celdas) else ''

        dt = parsear_fecha(celda('fecha'), celda('hora'), tz, formato_cache)
        if not dt:
            continue
        ms = int(dt.timestamp() * 1000)

        v = numero(celda('sgv'))
        if v and 30 <= v <= 500:
            sgv[ms] = int(round(v))
        v = numero(celda('bg'))
        if v and 20 <= v <= 600:
            bg[ms] = int(round(v))
        v = numero(celda('bolo'))
        if v and v > 0:
            bolos[ms] = round(bolos.get(ms, 0) + v, 3)
        v = numero(celda('carbs'))
        if v and v > 0:
            carbs[ms] = v

    return {
        'delimitador': delim, 'primera_linea': primera[:120], 'columnas': sorted(columnas_vistas),
        'formato_fecha': formato_cache[0] if formato_cache else None,
        'sgv': sgv, 'bg': bg, 'bolos': bolos, 'carbs': carbs,
    }


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def armar_tratamientos(datos):
    """Une bolo + carbohidratos dentro de una ventana de 2 minutos en un 'Meal Bolus'."""
    tratamientos = []
    carbs_libres = dict(datos['carbs'])
    for ms, u in sorted(datos['bolos'].items()):
        par = next((c for c in carbs_libres if abs(c - ms) <= 120000), None)
        t = {'eventType': 'Correction Bolus', 'insulin': u, 'created_at': iso(ms), 'enteredBy': DEVICE}
        if par is not None:
            t['eventType'] = 'Meal Bolus'
            t['carbs'] = carbs_libres.pop(par)
        tratamientos.append(t)
    for ms, g in sorted(carbs_libres.items()):
        tratamientos.append({'eventType': 'Carb Correction', 'carbs': g, 'created_at': iso(ms), 'enteredBy': DEVICE})
    for ms, v in sorted(datos['bg'].items()):
        tratamientos.append({'eventType': 'BG Check', 'glucose': v, 'glucoseType': 'Finger',
                             'units': 'mg/dl', 'created_at': iso(ms), 'enteredBy': DEVICE})
    return tratamientos


class Nightscout:
    def __init__(self, url, secret):
        self.url = url.rstrip('/')
        self.headers = {'api-secret': hash_secret(secret), 'Content-Type': 'application/json',
                        'Accept': 'application/json'}

    def _req(self, metodo, ruta, cuerpo=None):
        data = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(self.url + ruta, data=data, headers=self.headers, method=metodo)
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode() or '[]'
            return json.loads(txt)

    def fechas_sgv(self, desde, hasta):
        q = urllib.parse.urlencode({'find[date][$gte]': desde, 'find[date][$lte]': hasta, 'count': 200000})
        return {e.get('date') for e in self._req('GET', f'/api/v1/entries/sgv.json?{q}')}

    def claves_tratamientos(self, desde, hasta):
        q = urllib.parse.urlencode({'find[created_at][$gte]': iso(desde), 'find[created_at][$lte]': iso(hasta),
                                    'count': 200000})
        existentes = self._req('GET', f'/api/v1/treatments.json?{q}')
        return {(t.get('eventType'), (t.get('created_at') or '')[:16]) for t in existentes}

    def subir(self, ruta, items):
        for i in range(0, len(items), LOTE):
            self._req('POST', ruta, items[i:i + LOTE])
            print(f'   {ruta}: {min(i + LOTE, len(items))}/{len(items)}')


def main():
    ap = argparse.ArgumentParser(description='Importa un CSV de CareLink a Nightscout')
    ap.add_argument('csv', help='Ruta al CSV exportado de CareLink')
    ap.add_argument('--dry-run', action='store_true', help='Solo analiza y muestra, no sube nada')
    ap.add_argument('--utc-offset', type=float, default=-3,
                    help='Huso horario de la bomba en horas (Argentina = -3, por defecto)')
    args = ap.parse_args()

    tz = timezone(timedelta(hours=args.utc_offset))
    datos = leer_carelink(args.csv, tz)

    print(f"Archivo: {args.csv}")
    print(f"Delimitador: '{datos['delimitador']}'  |  Formato de fecha: {datos['formato_fecha']}")
    print(f"Columnas reconocidas: {', '.join(datos['columnas']) or 'NINGUNA'}")
    print(f"Glucosa sensor: {len(datos['sgv'])}  |  Bolos: {len(datos['bolos'])}  |  "
          f"Carbs: {len(datos['carbs'])}  |  Glucemias capilares: {len(datos['bg'])}")

    if not datos['sgv'] and not datos['bolos'] and not datos['carbs']:
        print('\nNo se encontraron datos. Primera línea del archivo:', datos['primera_linea'])
        print('Pasale a Claude las primeras ~15 líneas del CSV para ajustar el mapeo de columnas.')
        sys.exit(1)

    todos = list(datos['sgv']) + list(datos['bolos']) + list(datos['carbs']) + list(datos['bg'])
    desde, hasta = min(todos), max(todos)
    print(f"Rango: {iso(desde)}  ->  {iso(hasta)}  (UTC)")

    entries = [{'type': 'sgv', 'sgv': v, 'date': ms, 'dateString': iso(ms), 'direction': 'NONE',
                'device': DEVICE} for ms, v in sorted(datos['sgv'].items())]
    tratamientos = armar_tratamientos(datos)

    if args.dry_run:
        print('\n--dry-run: no se sube nada. Ejemplos:')
        for e in entries[:3]:
            print('  entry     ', e)
        for t in tratamientos[:3]:
            print('  treatment ', t)
        return

    url, secret = os.environ.get('NIGHTSCOUT_URL'), os.environ.get('NIGHTSCOUT_API_SECRET')
    if not url or not secret:
        sys.exit('Faltan las variables de entorno NIGHTSCOUT_URL y/o NIGHTSCOUT_API_SECRET.')
    ns = Nightscout(url, secret)

    ya = ns.fechas_sgv(desde - 60000, hasta + 60000)
    ya = sorted(d for d in ya if isinstance(d, (int, float)))

    def existe(ms):  # ya hay una lectura a menos de 60 s (p. ej. cargada por otro medio)
        i = bisect.bisect_left(ya, ms - 60000)
        return i < len(ya) and ya[i] <= ms + 60000

    entries = [e for e in entries if not existe(e['date'])]
    ya_t = ns.claves_tratamientos(desde - 60000, hasta + 60000)
    tratamientos = [t for t in tratamientos if (t['eventType'], t['created_at'][:16]) not in ya_t]
    print(f"\nNuevos a subir -> entries: {len(entries)}  |  treatments: {len(tratamientos)}")

    if entries:
        ns.subir('/api/v1/entries.json', entries)
    if tratamientos:
        ns.subir('/api/v1/treatments.json', tratamientos)
    print('Listo.')


if __name__ == '__main__':
    main()

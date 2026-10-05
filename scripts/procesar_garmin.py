"""
Glucast / GlucoAI — Procesador de datos Garmin
================================================
Lee el ZIP exportado desde connect.garmin.com (Cuenta > Exportar tus datos)
y genera:
  1. Un CSV consolidado por día (estrés, FC, sueño, actividad) listo para el modelo LSTM
  2. Sube esos mismos datos a Nightscout como "treatments" tipo Note, para verlos en GlucoAI

Uso:
    python3 procesar_garmin.py /ruta/al/datos_garmin.zip
"""

import json
import os
import csv
import sys
import zipfile
import hashlib
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Configuración por variables de entorno (ver .env.example)
NIGHTSCOUT_URL = os.environ['NIGHTSCOUT_URL']
API_SECRET = os.environ['NIGHTSCOUT_API_SECRET']
BATCH_SIZE = 50


def hash_secret(secret):
    return hashlib.sha1(secret.encode()).hexdigest()


def cargar_json_de_zip(zf, prefijo, sufijo):
    """Carga y concatena todos los archivos JSON dentro del zip que matcheen un patrón"""
    registros = []
    for nombre in zf.namelist():
        if prefijo in nombre and nombre.endswith(sufijo):
            with zf.open(nombre) as f:
                try:
                    data = json.load(f)
                    if isinstance(data, list):
                        registros.extend(data)
                    else:
                        registros.append(data)
                except json.JSONDecodeError:
                    continue
    return registros


def procesar_estres_y_fc(registros_uds):
    """Extrae estrés, FC y actividad diaria de los archivos UDSFile_*.json"""
    por_dia = {}
    for r in registros_uds:
        fecha = r.get('calendarDate')
        if not fecha:
            continue

        stress = r.get('allDayStress', {})
        stress_total = next(
            (a for a in stress.get('aggregatorList', []) if a.get('type') == 'TOTAL'),
            {}
        )

        por_dia[fecha] = {
            'fecha': fecha,
            'estres_promedio': stress_total.get('averageStressLevel'),
            'estres_maximo': stress_total.get('maxStressLevel'),
            'estres_duracion_alto_seg': stress_total.get('stressDuration'),
            'estres_duracion_descanso_seg': stress_total.get('restDuration'),
            'fc_minima': r.get('minHeartRate'),
            'fc_maxima': r.get('maxHeartRate'),
            'fc_reposo': r.get('restingHeartRate'),
            'pasos_totales': r.get('totalSteps'),
            'calorias_totales': r.get('totalKilocalories'),
            'calorias_activas': r.get('activeKilocalories'),
            'distancia_metros': r.get('totalDistanceMeters'),
            'minutos_intensidad_moderada': r.get('moderateIntensityMinutes'),
            'minutos_intensidad_vigorosa': r.get('vigorousIntensityMinutes'),
        }
    return por_dia


def procesar_sueno(registros_sleep):
    """Extrae duración y calidad de sueño por día"""
    por_dia = {}
    for r in registros_sleep:
        fecha = r.get('calendarDate')
        if not fecha:
            continue

        deep = r.get('deepSleepSeconds') or 0
        light = r.get('lightSleepSeconds') or 0
        awake = r.get('awakeSleepSeconds') or 0
        total_seg = deep + light

        por_dia[fecha] = {
            'sueno_inicio': r.get('sleepStartTimestampGMT'),
            'sueno_fin': r.get('sleepEndTimestampGMT'),
            'sueno_profundo_seg': deep,
            'sueno_liviano_seg': light,
            'sueno_despierto_seg': awake,
            'sueno_total_horas': round(total_seg / 3600, 2) if total_seg else None,
        }
    return por_dia


def combinar(estres_fc, sueno):
    """Combina ambos diccionarios por fecha en una sola lista ordenada"""
    todas_fechas = sorted(set(estres_fc.keys()) | set(sueno.keys()))
    filas = []
    for fecha in todas_fechas:
        fila = {'fecha': fecha}
        fila.update(estres_fc.get(fecha, {}))
        fila.update(sueno.get(fecha, {}))
        filas.append(fila)
    return filas


def guardar_csv(filas, ruta_salida):
    if not filas:
        print("No hay datos para guardar en CSV.")
        return

    columnas = [
        'fecha', 'estres_promedio', 'estres_maximo',
        'estres_duracion_alto_seg', 'estres_duracion_descanso_seg',
        'fc_minima', 'fc_maxima', 'fc_reposo',
        'pasos_totales', 'calorias_totales', 'calorias_activas', 'distancia_metros',
        'minutos_intensidad_moderada', 'minutos_intensidad_vigorosa',
        'sueno_inicio', 'sueno_fin', 'sueno_profundo_seg', 'sueno_liviano_seg',
        'sueno_despierto_seg', 'sueno_total_horas',
    ]

    with open(ruta_salida, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)

    print(f"CSV guardado: {ruta_salida} ({len(filas)} días)")


def subir_a_nightscout(filas):
    """Sube un resumen diario de estrés/sueño/FC a Nightscout como notas (treatments)"""
    secret_hash = hash_secret(API_SECRET)
    treatments = []

    for fila in filas:
        if fila.get('estres_promedio') is None and fila.get('sueno_total_horas') is None:
            continue

        try:
            dt = datetime.strptime(fila['fecha'], '%Y-%m-%d').replace(
                hour=23, minute=59, tzinfo=timezone.utc
            )
        except (ValueError, KeyError):
            continue

        partes = []
        if fila.get('estres_promedio') is not None:
            partes.append(f"Estrés prom: {fila['estres_promedio']} (máx {fila.get('estres_maximo', '-')})")
        if fila.get('fc_reposo') is not None:
            partes.append(f"FC reposo: {fila['fc_reposo']} bpm")
        if fila.get('sueno_total_horas') is not None:
            partes.append(f"Sueño: {fila['sueno_total_horas']}h")
        if fila.get('pasos_totales') is not None:
            partes.append(f"Pasos: {fila['pasos_totales']}")

        notas = ' | '.join(partes)

        treatments.append({
            'eventType': 'Note',
            'created_at': dt.isoformat(),
            'notes': f"Garmin — {notas}",
            'enteredBy': 'Glucast-GarminImport'
        })

    if not treatments:
        print("No hay datos válidos de Garmin para subir a Nightscout.")
        return

    batches = [treatments[i:i + BATCH_SIZE] for i in range(0, len(treatments), BATCH_SIZE)]
    print(f"\nSubiendo {len(treatments)} registros diarios de Garmin a Nightscout en {len(batches)} lotes...")

    total_ok = 0
    for i, batch in enumerate(batches):
        data = json.dumps(batch).encode('utf-8')
        req = urllib.request.Request(
            f'{NIGHTSCOUT_URL}/api/v1/treatments',
            data=data,
            headers={'Content-Type': 'application/json', 'api-secret': secret_hash},
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                total_ok += len(batch)
                print(f"  Lote {i+1}/{len(batches)} ✓")
        except Exception as e:
            print(f"  Lote {i+1}/{len(batches)} ERROR: {e}")
        time.sleep(0.3)

    print(f"\nCompletado: {total_ok} registros subidos a Nightscout.")


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 procesar_garmin.py /ruta/al/datos_garmin.zip")
        sys.exit(1)

    ruta_zip = Path(sys.argv[1]).expanduser()
    if not ruta_zip.exists():
        print(f"ERROR: no se encontró el archivo {ruta_zip}")
        sys.exit(1)

    print("=" * 55)
    print("  Glucast — Procesador de datos Garmin")
    print("=" * 55)
    print(f"\nLeyendo {ruta_zip.name}...")

    with zipfile.ZipFile(ruta_zip, 'r') as zf:
        registros_uds = cargar_json_de_zip(zf, 'DI-Connect-Aggregator/UDSFile_', '.json')
        registros_sleep = cargar_json_de_zip(zf, 'DI-Connect-Wellness/', 'sleepData.json')

    print(f"Registros diarios (estrés/FC/actividad): {len(registros_uds)}")
    print(f"Registros de sueño: {len(registros_sleep)}")

    estres_fc = procesar_estres_y_fc(registros_uds)
    sueno = procesar_sueno(registros_sleep)
    filas = combinar(estres_fc, sueno)

    print(f"Total de días consolidados: {len(filas)}")

    ruta_csv = ruta_zip.parent / 'garmin_datos_consolidados.csv'
    guardar_csv(filas, ruta_csv)

    subir_a_nightscout(filas)

    print("\n" + "=" * 55)
    print("  Proceso completado")
    print("=" * 55)


if __name__ == '__main__':
    main()

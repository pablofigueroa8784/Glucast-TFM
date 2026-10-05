"""
Glucast — Vision Server
========================
Servidor Flask que recibe una imagen en base64 desde la app Glucast,
la analiza con Google Cloud Vision API para identificar el alimento,
consulta Open Food Facts para obtener los carbohidratos,
y devuelve el resultado con sugerencia de insulina.

Uso local:
    pip install flask google-cloud-vision requests
    export GOOGLE_APPLICATION_CREDENTIALS=/ruta/a/credenciales-google-cloud.json
    python3 server.py

Deploy en Railway:
    - Agregar variable de entorno GOOGLE_CREDENTIALS_JSON con el contenido del JSON
    - Railway detecta el Procfile y corre el servidor
"""

import os
import json
import base64
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify
from google.cloud import vision
from google.oauth2 import service_account

app = Flask(__name__)


@app.after_request
def agregar_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp

# ── Configuración ──────────────────────────────────────────────────────────────


# Pesos estimados por tamaño de porción (en gramos)
# El usuario elige S/M/L desde la app
PESOS_POR_TAMANO = {
    'S': 0.6,   # porción chica = 60% del peso base
    'M': 1.0,   # porción mediana = peso base
    'L': 1.5,   # porción grande = 150% del peso base
}

# Pesos base por categoría de alimento (gramos por porción estándar)
PESOS_BASE = {
    'sandwich': 300,
    'hot dog bun': 200,
    'hamburger bun': 180,
    'bread': 150,
    'bun': 180,
    'pizza': 200,
    'pasta': 200,
    'rice': 150,
    'cracker': 30,
    'cookie': 40,
    'cake': 100,
    'fruit': 150,
    'apple': 150,
    'banana': 120,
    'orange': 130,
    'potato': 150,
    'french fries': 150,
    'salad': 200,
    'meat': 150,
    'chicken': 150,
    'fish': 150,
    'egg': 50,
    'milk': 200,
    'yogurt': 150,
    'cheese': 30,
    'default': 150,
}

# Factor adicional por relleno según contexto detectado
FACTOR_RELLENO = {
    'fast food': 1.2,
    'sandwich': 1.15,
    'burger': 1.2,
    'hot dog': 1.1,
}

# ── Cliente Vision API ─────────────────────────────────────────────────────────

def get_vision_client():
    """Crea el cliente de Vision API usando credenciales del entorno"""
    # En Railway: credenciales como variable de entorno JSON
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if creds_json:
        creds_dict = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        return vision.ImageAnnotatorClient(credentials=credentials)
    # En local: usa GOOGLE_APPLICATION_CREDENTIALS
    return vision.ImageAnnotatorClient()

# ── Funciones principales ──────────────────────────────────────────────────────

def identificar_alimento(imagen_base64):
    """Llama a Vision API y devuelve los labels detectados"""
    client = get_vision_client()
    image_bytes = base64.b64decode(imagen_base64)
    image = vision.Image(content=image_bytes)
    response = client.label_detection(image=image)
    labels = [
        {'nombre': l.description, 'confianza': round(l.score, 2)}
        for l in response.label_annotations[:8]
    ]
    return labels


def buscar_carbohidratos_off(query):
    """
    Busca los carbohidratos del alimento en Open Food Facts.
    Intenta primero en español, luego en inglés.
    Open Food Facts es global y tiene productos latinoamericanos.
    """
    # Traducciones comunes de labels de Vision API (inglés → español)
    TRADUCCIONES = {
        'bread': 'pan', 'sandwich': 'sandwich', 'pizza': 'pizza',
        'pasta': 'pasta', 'rice': 'arroz', 'cookie': 'galleta',
        'cake': 'torta', 'apple': 'manzana', 'banana': 'banana',
        'orange': 'naranja', 'potato': 'papa', 'french fries': 'papas fritas',
        'chicken': 'pollo', 'beef': 'carne', 'fish': 'pescado',
        'egg': 'huevo', 'milk': 'leche', 'yogurt': 'yogur',
        'cheese': 'queso', 'ham': 'jamon', 'sausage': 'salchicha',
        'empanada': 'empanada', 'medialunas': 'medialuna',
        'hot dog bun': 'pan de pancho', 'hamburger bun': 'pan de hamburguesa',
        'fast food': 'comida rapida', 'cracker': 'galleta',
        'salad': 'ensalada', 'soup': 'sopa', 'steak': 'bife',
    }

    query_lower = query.lower()
    query_es = TRADUCCIONES.get(query_lower, query_lower)

    # Intentar en español primero, luego en inglés
    for lang, q in [('es', query_es), ('en', query_lower)]:
        try:
            url = f'https://world.openfoodfacts.org/cgi/search.pl?search_terms={urllib.parse.quote(q)}&search_simple=1&action=process&json=1&page_size=5&lc={lang}'
            req = urllib.request.Request(url, headers={
                'Accept': 'application/json',
                'User-Agent': 'Glucast-TFM/1.0 (pablo.figueroa@rectorado.unt.edu.ar)'
            })
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read())

            products = data.get('products', [])
            for product in products:
                nombre = product.get('product_name', '') or product.get('product_name_es', '')
                nutriments = product.get('nutriments', {})
                carbs = nutriments.get('carbohydrates_100g') or nutriments.get('carbohydrates')
                if carbs is not None and float(carbs) > 0:
                    return nombre or q, float(carbs)
        except Exception as e:
            print(f'Error Open Food Facts ({lang}): {e}')
            continue

    # Fallback: tabla de carbohidratos locales para alimentos comunes en Argentina
    TABLA_LOCAL = {
        'pan': 50, 'sandwich': 48, 'medialuna': 45, 'empanada': 35,
        'pizza': 30, 'pasta': 25, 'arroz': 28, 'papa': 17,
        'papas fritas': 35, 'pollo': 0, 'carne': 0, 'bife': 0,
        'huevo': 1, 'leche': 5, 'yogur': 12, 'queso': 2,
        'manzana': 14, 'banana': 23, 'naranja': 12,
        'galleta': 65, 'torta': 55, 'cookie': 65,
        'pan de pancho': 50, 'pan de hamburguesa': 48,
        'bread': 50, 'hot dog bun': 50, 'hamburger bun': 48,
        'fast food': 40, 'cracker': 65,
        # Platos típicos argentinos/latinoamericanos
        'guiso': 15, 'guiso de lentejas': 15, 'lentejas': 20,
        'guiso de arroz': 22, 'locro': 12, 'carbonada': 14,
        'milanesa': 15, 'milanesa napolitana': 16,
        'asado': 0, 'choripan': 30, 'chorizo': 2,
        'facturas': 45, 'medialunas': 45, 'vigilante': 48,
        'alfajor': 65, 'dulce de leche': 55,
        'mate cocido': 5, 'submarino': 10,
        'fideos': 25, 'fideos con salsa': 22, 'ñoquis': 20,
        'polenta': 10, 'humita': 22,
        'ensalada rusa': 10, 'ensalada mixta': 5,
        'tarta': 25, 'pascualina': 18,
        'sopa': 8, 'caldo': 3, 'puchero': 10,
        'revuelto gramajo': 12, 'provoleta': 2,
        'rice': 28, 'lentils': 20, 'stew': 12,
    }
    carbs_local = TABLA_LOCAL.get(query_lower) or TABLA_LOCAL.get(query_es)
    if carbs_local:
        return query_es, carbs_local

    return None, None


def estimar_peso_base(labels):
    """Estima el peso base de la porción según el alimento detectado"""
    peso_base = PESOS_BASE['default']
    categoria = 'food'
    factor = 1.0

    # Buscar categoría principal
    for label in labels:
        nombre_lower = label['nombre'].lower()
        for cat, peso in PESOS_BASE.items():
            if cat in nombre_lower:
                peso_base = peso
                categoria = cat
                break
        if categoria != 'food':
            break

    # Aplicar factor de relleno si corresponde
    for label in labels:
        nombre_lower = label['nombre'].lower()
        for contexto, f in FACTOR_RELLENO.items():
            if contexto in nombre_lower:
                factor = max(factor, f)

    return round(peso_base * factor), categoria


def calcular_insulina(carbs_gramos, ic_ratio=12):
    """Calcula las unidades de insulina sugeridas según el ratio I:C"""
    return round(carbs_gramos / ic_ratio, 1)

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'Glucast Vision Server'})


@app.route('/analizar', methods=['POST'])
def analizar():
    """
    Recibe una imagen en base64 y opcionalmente el nombre del plato, devuelve:
    - alimento detectado
    - carbohidratos totales por tamaño (S/M/L)
    - insulina sugerida por tamaño

    Body JSON:
    {
        "imagen": "<base64>",
        "nombre_plato": "guiso de lentejas",  (opcional - mejora la precisión)
        "ic_ratio": 12  (opcional, default 12)
    }
    """
    try:
        data = request.get_json()
        if not data or 'imagen' not in data:
            return jsonify({'error': 'Se requiere el campo imagen en base64'}), 400

        imagen_b64 = data['imagen']
        ic_ratio = data.get('ic_ratio', 12)
        nombre_plato = data.get('nombre_plato', '').strip()

        # 1. Identificar alimento con Vision API
        labels = identificar_alimento(imagen_b64)
        if not labels:
            return jsonify({'error': 'No se pudo identificar el alimento'}), 400

        # 2. Buscar carbohidratos
        # Si el usuario escribió el nombre del plato, usarlo primero (más preciso)
        nombre_encontrado = None
        carbs_por_100g = None
        query_usada = None

        if nombre_plato:
            nombre_encontrado, carbs_por_100g = buscar_carbohidratos_off(nombre_plato)
            if carbs_por_100g is not None:
                query_usada = nombre_plato

        # Si no hay nombre manual o no encontró, usar los labels de Vision
        if carbs_por_100g is None:
            LABELS_GENERICOS = {'food', 'ingredient', 'dish', 'cuisine', 'meal',
                               'recipe', 'snack', 'produce', 'staple food'}
            labels_especificos = [l for l in labels
                                 if l['nombre'].lower() not in LABELS_GENERICOS]
            labels_busqueda = labels_especificos if labels_especificos else labels

            for label in labels_busqueda[:5]:
                nombre_encontrado, carbs_por_100g = buscar_carbohidratos_off(label['nombre'])
                if carbs_por_100g is not None:
                    query_usada = label['nombre']
                    break

        if carbs_por_100g is None:
            return jsonify({
                'labels': labels,
                'error': 'No se encontraron datos nutricionales para este alimento',
                'sugerencia': 'Escribí el nombre del plato en el campo de texto e intentá de nuevo'
            }), 200

        # 3. Estimar peso por tamaño
        # Si hay nombre de plato del usuario, usarlo para estimar el peso
        labels_para_peso = [{'nombre': nombre_plato}] + labels if nombre_plato else labels
        peso_base, categoria = estimar_peso_base(labels_para_peso)

        porciones = {}
        for tamano, factor in PESOS_POR_TAMANO.items():
            peso_g = round(peso_base * factor)
            carbs_totales = round(peso_g * carbs_por_100g / 100, 1)
            insulina_u = calcular_insulina(carbs_totales, ic_ratio)
            porciones[tamano] = {
                'peso_g': peso_g,
                'carbs_totales': carbs_totales,  # total, no por 100g
                'insulina_u': insulina_u
            }

        return jsonify({
            'labels': labels[:5],
            'alimento_detectado': query_usada,
            'alimento_nombre': nombre_encontrado,
            'carbs_por_100g': carbs_por_100g,
            'categoria': categoria,
            'porciones': porciones,
            'ic_ratio_usado': ic_ratio,
            'nombre_manual': nombre_plato or None
        })

    except Exception as e:
        print(f'Error en /analizar: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f'Glucast Vision Server corriendo en puerto {port}')
    app.run(host='0.0.0.0', port=port, debug=False)

"""
Glucast — Vision Server
========================
Servidor Flask que recibe una imagen en base64 desde la app Glucast,
la analiza con Google Cloud Vision API para identificar el alimento,
consulta Open Food Facts para obtener los carbohidratos,
y devuelve el resultado con sugerencia de insulina.

Uso local:
    pip install -r requirements.txt
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

# Peso de una porción mediana (gramos) por alimento, con las mismas claves en
# español que TABLA_LOCAL. Los valores son referencias aproximadas de plato casero.
PESOS_BASE = {
    'sandwich': 200, 'hamburguesa': 220, 'pancho': 150, 'choripan': 200,
    'pan': 60, 'pan de pancho': 50, 'pan de hamburguesa': 60, 'tostada': 30,
    'medialuna': 50, 'facturas': 60, 'vigilante': 60, 'empanada': 90,
    'pizza': 250, 'pasta': 250, 'fideos': 250, 'fideos con salsa': 300,
    'ñoquis': 250, 'lasaña': 300, 'ravioles': 250, 'canelones': 300,
    'arroz': 180, 'papa': 150, 'pure de papa': 200, 'papas fritas': 150,
    'batata': 150, 'choclo': 120,
    'galleta': 30, 'torta': 100, 'tarta': 150, 'pascualina': 150, 'dona': 60,
    'muffin': 80, 'panqueque': 80, 'alfajor': 50, 'helado': 120, 'chocolate': 25,
    'manzana': 150, 'banana': 120, 'naranja': 130, 'mandarina': 90, 'pera': 160,
    'uva': 120, 'frutilla': 150, 'sandia': 250, 'melon': 200, 'durazno': 150,
    'kiwi': 75, 'anana': 150, 'mango': 200,
    'ensalada': 200, 'ensalada rusa': 200, 'ensalada mixta': 200,
    'carne': 200, 'bife': 200, 'asado': 250, 'pollo': 200, 'pollo frito': 200,
    'pescado': 180, 'milanesa': 180, 'milanesa napolitana': 220,
    'milanesa con pure': 380, 'milanesa con papas fritas': 330,
    'huevo': 50, 'queso': 30, 'jamon': 30,
    'leche': 250, 'yogur': 190, 'jugo': 250, 'jugo de naranja': 250, 'gaseosa': 350,
    'cereal': 40, 'avena': 250,
    'guiso': 350, 'guiso de lentejas': 350, 'guiso de arroz': 350, 'lentejas': 200,
    'porotos': 200, 'garbanzos': 200, 'locro': 350, 'carbonada': 350,
    'puchero': 400, 'sopa': 300, 'caldo': 250, 'polenta': 250, 'humita': 250,
    'tortilla de papa': 200, 'sushi': 200, 'taco': 150, 'burrito': 250,
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

# ── Diccionarios de alimentos ──────────────────────────────────────────────────

# Labels de Vision que no describen un alimento concreto (se ignoran en la búsqueda)
LABELS_GENERICOS = {
    'food', 'ingredient', 'dish', 'cuisine', 'meal', 'recipe', 'snack', 'produce',
    'staple food', 'comfort food', 'fast food', 'finger food', 'junk food',
    'natural foods', 'whole food', 'vegetarian food', 'vegan nutrition',
    'superfood', 'local food', 'side dish', 'garnish', 'appetizer', 'lunch',
    'breakfast', 'dinner', 'brunch', 'supper', 'baked goods', 'dessert',
    'american food', 'italian food', 'mexican food', 'european food',
    'latin american food', 'spanish cuisine', 'argentine cuisine', 'food group',
    'plant', 'leaf vegetable', 'vegetable', 'fruit', 'flavor', 'drink',
    # Vajilla, mesa y fotografía
    'tableware', 'dishware', 'serveware', 'plate', 'bowl', 'platter', 'table',
    'cutlery', 'fork', 'spoon', 'knife', 'kitchen utensil', 'drinkware',
    'tablecloth', 'wood', 'photograph', 'still life', 'still life photography',
    'close-up', 'circle', 'rectangle', 'metal', 'glass', 'plastic', 'paper',
    'cup', 'mug', 'saucer', 'container', 'kitchen', 'countertop', 'interior design',
}

# Traducciones de labels de Vision (inglés → español)
TRADUCCIONES = {
    'bread': 'pan', 'sliced bread': 'pan', 'white bread': 'pan', 'baguette': 'pan',
    'sandwich': 'sandwich', 'pizza': 'pizza', 'pasta': 'pasta', 'spaghetti': 'fideos',
    'noodle': 'fideos', 'noodles': 'fideos', 'gnocchi': 'ñoquis', 'lasagne': 'lasaña',
    'rice': 'arroz', 'white rice': 'arroz', 'fried rice': 'arroz', 'risotto': 'arroz',
    'cookie': 'galleta', 'cookies and crackers': 'galleta', 'cracker': 'galleta',
    'cake': 'torta', 'chocolate cake': 'torta', 'pie': 'tarta', 'quiche': 'tarta',
    'apple': 'manzana', 'banana': 'banana', 'orange': 'naranja', 'tangerine': 'mandarina',
    'pear': 'pera', 'grape': 'uva', 'grapes': 'uva', 'strawberry': 'frutilla',
    'watermelon': 'sandia', 'melon': 'melon', 'peach': 'durazno', 'kiwifruit': 'kiwi',
    'pineapple': 'anana', 'mango': 'mango',
    'potato': 'papa', 'mashed potato': 'pure de papa', 'french fries': 'papas fritas',
    'potato wedges': 'papas fritas', 'sweet potato': 'batata', 'corn': 'choclo',
    'sweet corn': 'choclo', 'corn on the cob': 'choclo', 'squash': 'zapallo',
    'pumpkin': 'zapallo', 'carrot': 'zanahoria', 'tomato': 'tomate', 'lettuce': 'lechuga',
    'chicken': 'pollo', 'fried chicken': 'pollo frito', 'chicken meat': 'pollo',
    'beef': 'carne', 'meat': 'carne', 'red meat': 'carne', 'steak': 'bife',
    'pork': 'cerdo', 'fish': 'pescado', 'egg': 'huevo', 'fried egg': 'huevo',
    'milk': 'leche', 'yogurt': 'yogur', 'cheese': 'queso', 'ham': 'jamon',
    'sausage': 'salchicha', 'hot dog': 'pancho', 'hamburger': 'hamburguesa',
    'cheeseburger': 'hamburguesa', 'veggie burger': 'hamburguesa',
    'empanada': 'empanada', 'dumpling': 'empanada', 'pastel': 'empanada',
    'croissant': 'medialuna', 'medialunas': 'medialuna', 'pastry': 'facturas',
    'danish pastry': 'facturas', 'viennoiserie': 'facturas', 'puff pastry': 'facturas',
    'doughnut': 'dona', 'muffin': 'muffin', 'pancake': 'panqueque', 'crepe': 'panqueque',
    'waffle': 'waffle', 'toast': 'tostada', 'breakfast cereal': 'cereal',
    'hot dog bun': 'pan de pancho', 'hamburger bun': 'pan de hamburguesa', 'bun': 'pan',
    'salad': 'ensalada', 'soup': 'sopa', 'stew': 'guiso', 'lentil': 'lentejas',
    'lentils': 'lentejas', 'lentil soup': 'guiso de lentejas', 'beans': 'porotos',
    'chickpea': 'garbanzos', 'schnitzel': 'milanesa', 'cutlet': 'milanesa',
    'breaded cutlet': 'milanesa', 'chicken fried steak': 'milanesa',
    'milanesa': 'milanesa', 'polenta': 'polenta', 'tamale': 'humita',
    'alfajor': 'alfajor', 'dulce de leche': 'dulce de leche', 'ice cream': 'helado',
    'chocolate': 'chocolate', 'juice': 'jugo', 'orange juice': 'jugo de naranja',
    'soft drink': 'gaseosa', 'cola': 'gaseosa', 'coffee': 'cafe', 'tea': 'te',
    'oatmeal': 'avena', 'porridge': 'avena', 'tortilla': 'tortilla', 'taco': 'taco',
    'burrito': 'burrito', 'sushi': 'sushi',
}

# Carbohidratos por 100 g de alimentos comunes en Argentina (valores de referencia
# aproximados, preparados/cocidos). Se consulta ANTES que Open Food Facts porque OFF
# devuelve productos envasados que no representan un plato casero.
TABLA_LOCAL = {
    'pan': 50, 'sandwich': 30, 'medialuna': 45, 'empanada': 30, 'facturas': 45,
    'vigilante': 48, 'pizza': 30, 'pasta': 25, 'fideos': 25, 'fideos con salsa': 22,
    'ñoquis': 30, 'lasaña': 15, 'ravioles': 25, 'canelones': 15,
    'arroz': 28, 'papa': 17, 'pure de papa': 15, 'papas fritas': 35, 'batata': 20,
    'choclo': 19, 'zapallo': 7, 'zanahoria': 8, 'tomate': 4, 'lechuga': 2,
    'pollo': 0, 'pollo frito': 10, 'carne': 0, 'bife': 0, 'cerdo': 0, 'pescado': 0,
    'asado': 0, 'jamon': 1, 'salchicha': 3, 'chorizo': 2, 'huevo': 1,
    'leche': 5, 'yogur': 12, 'queso': 2, 'helado': 24,
    'manzana': 14, 'banana': 23, 'naranja': 12, 'mandarina': 13, 'pera': 15,
    'uva': 17, 'frutilla': 8, 'sandia': 8, 'melon': 8, 'durazno': 10, 'kiwi': 15,
    'anana': 13, 'mango': 15,
    'galleta': 65, 'torta': 55, 'tarta': 25, 'pascualina': 18, 'dona': 50,
    'muffin': 50, 'panqueque': 28, 'waffle': 40, 'tostada': 50, 'cereal': 80,
    'avena': 12, 'pan de pancho': 50, 'pan de hamburguesa': 48,
    'hamburguesa': 25, 'pancho': 25, 'choripan': 30, 'comida rapida': 35,
    'guiso': 15, 'guiso de lentejas': 15, 'lentejas': 20, 'guiso de arroz': 22,
    'porotos': 22, 'garbanzos': 27, 'locro': 12, 'carbonada': 14,
    'milanesa': 15, 'milanesa napolitana': 16, 'milanesa con pure': 15,
    'milanesa con papas fritas': 25,
    'alfajor': 65, 'dulce de leche': 55, 'chocolate': 55,
    'mate cocido': 5, 'submarino': 10, 'jugo': 10, 'jugo de naranja': 10,
    'gaseosa': 11, 'cafe': 0, 'te': 0,
    'polenta': 10, 'humita': 22, 'ensalada': 5, 'ensalada rusa': 10,
    'ensalada mixta': 5, 'sopa': 8, 'caldo': 3, 'puchero': 10,
    'revuelto gramajo': 12, 'provoleta': 2, 'tortilla': 45, 'tortilla de papa': 15,
    'taco': 20, 'burrito': 25, 'sushi': 30,
}


def normalizar(texto):
    """Minúsculas, sin acentos (salvo ñ) y sin espacios sobrantes"""
    t = (texto or '').lower().strip()
    for a, b in (('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u'), ('ü', 'u')):
        t = t.replace(a, b)
    return ' '.join(t.split())


def es_generico(nombre):
    return normalizar(nombre) in LABELS_GENERICOS

# ── Funciones principales ──────────────────────────────────────────────────────

def identificar_alimento(imagen_base64):
    """
    Llama a Vision API (labels + web detection en una sola solicitud).
    Devuelve los labels ordenados por confianza; el "best guess" de web detection
    va primero porque suele nombrar el plato concreto (ej. "milanesa napolitana")
    mientras que label_detection devuelve categorías ("Food", "Fried food").
    """
    client = get_vision_client()
    image_bytes = base64.b64decode(imagen_base64)
    image = vision.Image(content=image_bytes)
    response = client.annotate_image({
        'image': image,
        'features': [
            {'type_': vision.Feature.Type.LABEL_DETECTION, 'max_results': 15},
            {'type_': vision.Feature.Type.WEB_DETECTION, 'max_results': 5},
        ],
    })
    if response.error.message:
        raise RuntimeError(f'Vision API: {response.error.message}')

    labels = []
    vistos = set()

    def agregar(nombre, confianza, fuente):
        clave = normalizar(nombre)
        if clave and clave not in vistos:
            vistos.add(clave)
            labels.append({'nombre': nombre, 'confianza': round(confianza, 2), 'fuente': fuente})

    web = response.web_detection
    if web:
        for guess in web.best_guess_labels:
            agregar(guess.label, 0.9, 'web')
        for entidad in web.web_entities[:5]:
            if entidad.description and entidad.score >= 0.5:
                agregar(entidad.description, min(entidad.score, 0.89), 'web')

    for l in response.label_annotations:
        agregar(l.description, l.score, 'label')

    return labels


def buscar_tabla_local(query):
    """
    Busca en la tabla local. Primero coincidencia exacta (en inglés traducido o en
    español), luego la clave más larga contenida en el texto, así
    "milanesa con ensalada" encuentra "milanesa".
    Devuelve (nombre, carbs_100g) o (None, None). Los alimentos con 0 g también cuentan.
    """
    q = normalizar(query)
    q_es = TRADUCCIONES.get(q, q)
    for clave in (q_es, q):
        if clave in TABLA_LOCAL:
            return clave, float(TABLA_LOCAL[clave])

    # Coincidencia por palabras: la clave más larga que aparece en el texto
    texto = f' {q_es} '
    texto_en = f' {q} '
    mejor = None
    for clave in TABLA_LOCAL:
        if f' {clave} ' in texto and (mejor is None or len(clave) > len(mejor)):
            mejor = clave
    for en, es in TRADUCCIONES.items():
        if es in TABLA_LOCAL and f' {en} ' in texto_en and (mejor is None or len(es) > len(mejor)):
            mejor = es
    if mejor:
        return mejor, float(TABLA_LOCAL[mejor])
    return None, None


def buscar_carbohidratos_off(query):
    """
    Busca en Open Food Facts y devuelve la MEDIANA de carbohidratos/100 g de los
    productos cuyo nombre contiene el término buscado (antes se tomaba el primer
    producto, que podía ser cualquier cosa: ej. "chicken" → nuggets rebozados).
    """
    q = normalizar(query)
    q_es = TRADUCCIONES.get(q, q)
    for lang, termino in [('es', q_es), ('en', q)]:
        try:
            url = ('https://world.openfoodfacts.org/cgi/search.pl?search_terms='
                   f'{urllib.parse.quote(termino)}&search_simple=1&action=process'
                   f'&json=1&page_size=20&lc={lang}&fields=product_name,product_name_es,nutriments')
            req = urllib.request.Request(url, headers={
                'Accept': 'application/json',
                'User-Agent': 'Glucast-TFM/1.0 (pablo.figueroa@rectorado.unt.edu.ar)'
            })
            with urllib.request.urlopen(req, timeout=8) as res:
                data = json.loads(res.read())

            valores = []
            nombre_ejemplo = None
            for product in data.get('products', []):
                nombre = product.get('product_name_es') or product.get('product_name') or ''
                if termino.split()[0] not in normalizar(nombre):
                    continue
                carbs = product.get('nutriments', {}).get('carbohydrates_100g')
                try:
                    carbs = float(carbs)
                except (TypeError, ValueError):
                    continue
                if 0 <= carbs <= 100:
                    valores.append(carbs)
                    nombre_ejemplo = nombre_ejemplo or nombre
            if valores:
                valores.sort()
                mediana = valores[len(valores) // 2]
                return termino, round(mediana, 1)
        except Exception as e:
            print(f'Error Open Food Facts ({lang}): {e}')
            continue
    return None, None


def buscar_carbohidratos(query):
    """Tabla local primero (platos caseros), Open Food Facts como respaldo"""
    nombre, carbs = buscar_tabla_local(query)
    if carbs is not None:
        return nombre, carbs, 'tabla_local'
    nombre, carbs = buscar_carbohidratos_off(query)
    if carbs is not None:
        return nombre, carbs, 'open_food_facts'
    return None, None, None


def estimar_peso_base(alimento, labels):
    """
    Estima el peso de una porción mediana. Usa el alimento resuelto en la tabla
    local (ej. 'milanesa'); si no está, busca por palabras en los labels.
    """
    peso_base = PESOS_BASE['default']
    categoria = 'food'
    factor = 1.0

    candidatos = [alimento] + [l['nombre'] for l in labels]
    for nombre in candidatos:
        clave, _ = buscar_tabla_local(nombre or '')
        if clave in PESOS_BASE:
            peso_base = PESOS_BASE[clave]
            categoria = clave
            break

    # Aplicar factor de relleno si corresponde
    for label in labels:
        nombre_lower = normalizar(label['nombre'])
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
        ic_ratio = float(data.get('ic_ratio') or 12)
        nombre_plato = (data.get('nombre_plato') or '').strip()

        # 1. Identificar alimento con Vision API
        labels = identificar_alimento(imagen_b64)
        labels_especificos = [l for l in labels if not es_generico(l['nombre'])]

        # 2. Buscar carbohidratos. El nombre escrito por el usuario tiene prioridad;
        #    después se prueban los labels específicos en orden de confianza.
        consultas = ([nombre_plato] if nombre_plato else []) + [l['nombre'] for l in labels_especificos[:6]]

        candidatos = []
        vistos = set()
        for consulta in consultas:
            nombre, carbs, fuente = buscar_carbohidratos(consulta)
            if carbs is None or nombre in vistos:
                continue
            vistos.add(nombre)
            candidatos.append({'consulta': consulta, 'nombre': nombre,
                               'carbs_por_100g': carbs, 'fuente': fuente})
            # Con el nombre manual o 3 opciones alcanza; evita llamadas extra a OFF
            if (nombre_plato and consulta == nombre_plato) or len(candidatos) >= 3:
                break

        if not candidatos:
            return jsonify({
                'labels': labels[:8],
                'error': 'No se encontraron datos nutricionales para este alimento',
                'sugerencia': 'Escribí el nombre del plato en el campo de texto e intentá de nuevo'
            }), 200

        elegido = candidatos[0]
        carbs_por_100g = elegido['carbs_por_100g']

        # 3. Estimar peso por tamaño
        peso_base, categoria = estimar_peso_base(elegido['nombre'], labels_especificos)

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
            'labels': labels[:8],
            'alimento_detectado': elegido['nombre'],
            'alimento_nombre': elegido['nombre'],
            'consulta_usada': elegido['consulta'],
            'fuente': elegido['fuente'],
            'carbs_por_100g': carbs_por_100g,
            'categoria': categoria,
            'porciones': porciones,
            'candidatos': candidatos,
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

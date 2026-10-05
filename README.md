# Glucast 🩸📱

**Glucast** es una aplicación móvil multiplataforma (iOS/Android) desarrollada como Trabajo Final de Máster (TFM) de la **Maestría en Inteligencia Artificial** de la **Universidad Internacional de La Rioja (UNIR)**, en colaboración con la **Universidad Nacional de Tucumán (UNT)**, Argentina.

---

## 🎯 Objetivo

Glucast tiene como objetivo asistir a pacientes con **Diabetes Tipo 1 (T1D)** en la gestión de su condición mediante:

- Monitoreo continuo de glucosa en tiempo real (integración con CGM vía Nightscout)
- Registro de eventos clínicos (insulina, carbohidratos, ejercicio, notas)
- Visualización de tendencias históricas con análisis contextual (estrés, sueño, FC)
- **Predicción de glucosa futura** mediante modelos de Inteligencia Artificial (LSTM/Transformer) entrenados con datos propios del paciente

---

## 👤 Autor

**Pablo Daniel Figueroa**
Ingeniero en Sistemas — UTN Facultad Regional Tucumán
Maestría en Inteligencia Artificial — UNIR
Dirección General de Presupuesto — Universidad Nacional de Tucumán (UNT)
📧 pablo.figueroa@rectorado.unt.edu.ar

> El autor es paciente con Diabetes Tipo 1 desde los 18 meses de edad, usuario de bomba Medtronic MiniMed 780G con sensor CGM Guardian 4 y smartwatch Garmin Instinct. Los datos utilizados en el desarrollo y entrenamiento del modelo son **datos propios y reales**.

---

## 🏗️ Arquitectura

```
MiniMed 780G / Guardian 4
        ↓
   CareLink (CSV semanal)
        ↓
importar_nightscout.py (Mac)
        ↓
  Nightscout (Railway)  ←──  Garmin Export (procesar_garmin.py)
        ↓
   Glucast App (React Native / Expo)
        ↓
  Modelo LSTM/Transformer (Google Colab / TensorFlow) [en desarrollo]
```

---

## 📱 Stack Tecnológico

| Componente | Tecnología |
|---|---|
| App móvil | React Native + Expo Snack |
| Base de datos local | AsyncStorage |
| Backend CGM | Nightscout (Railway) |
| Base de datos nube | MongoDB (Railway) |
| Modelo IA | Python, TensorFlow, Google Colab |
| Datos CGM | Medtronic CareLink (CSV export) |
| Datos wearable | Garmin Connect (export ZIP) |

---

## ✅ Funcionalidades implementadas

- [x] Pantalla principal de registro (glucosa, insulina, carbohidratos, ejercicio, notas)
- [x] Autocomplete de glucosa desde CGM en tiempo real (Nightscout)
- [x] Pantalla de Tendencias con gráfico interactivo (pinch-to-zoom)
- [x] Filtros de rango temporal: 1h / 3h / 6h / 12h / 24h / 48h
- [x] Selector de fecha para ver cualquier día histórico
- [x] Estadísticas del período (promedio, mín, máx, % tiempo en rango)
- [x] Tarjeta de datos Garmin en Tendencias (estrés, FC, sueño, pasos)
- [x] Marcadores de bolos de insulina en el gráfico (💉)
- [x] Aviso automático cuando los datos llevan más de 7 días sin actualizar
- [x] Historial de registros con edición y eliminación
- [x] Script de importación CareLink → Nightscout (`importar_nightscout.py`)
- [x] Script de procesamiento Garmin → Nightscout + CSV (`procesar_garmin.py`)

---

## 🚧 En desarrollo / Trabajo futuro

- [ ] Modelo de predicción LSTM/Transformer (entrenamiento en Google Colab)
- [ ] Integración Nutritionix API (foto de alimento → estimación de carbohidratos)
- [ ] Integración Garmin Health API (datos en tiempo real con granularidad fina)
- [ ] Correlación automática hiper/hipoglucemia vs eventos (pasos, estrés, FC, insulina)
- [ ] Gesto de paneo en el gráfico (navegación libre en el tiempo)
- [ ] Instalación via TestFlight (salir de Expo Snack)
- [ ] Encriptación AES-256 de datos locales

---

## ⚠️ Limitaciones actuales y roadmap de integración CGM

### Limitación actual
La integración en tiempo real con **Medtronic MiniMed 780G / Guardian 4** no está disponible sin acceso a la API oficial de CareLink. El protocolo Bluetooth del sensor Guardian 4 está encriptado y no puede ser interceptado por aplicaciones de terceros. Se utiliza actualmente un flujo de **exportación manual semanal** del CSV de CareLink.

### Roadmap de integración para comercialización

| Plataforma | Estrategia |
|---|---|
| **iOS** | Integración vía API oficial de CareLink/Medtronic (solicitud en curso) |
| **Android** | Explorar integración vía xDrip+ como puente hacia Nightscout |
| **Genérico (recomendado)** | Integración con **Apple Health** (iOS) y **Google Health Connect** (Android) como capa de abstracción universal — cualquier CGM compatible automáticamente integrado con Glucast sin depender de APIs propietarias |

La estrategia de **Apple Health / Google Health Connect** es la más adecuada para una versión comercial, ya que permite compatibilidad con cualquier fabricante de CGM (Dexcom, Abbott FreeStyle Libre, Medtronic, etc.) sin necesidad de integraciones individuales.

---

## 📂 Scripts de utilidad

### `importar_nightscout.py`
Importa datos de glucosa y bolos de insulina desde el CSV de CareLink a Nightscout.

```bash
python3 ~/Downloads/importar_nightscout.py
```

Detecta automáticamente el CSV más reciente en `~/Downloads`.

### `procesar_garmin.py`
Procesa el ZIP exportado desde Garmin Connect y genera:
- CSV consolidado con estrés, FC, sueño y actividad por día
- Sube resumen diario a Nightscout como notas

```bash
python3 ~/Downloads/procesar_garmin.py ~/Desktop/"datos garmin.zip"
```

---

## 🔬 Dataset propio

El modelo de predicción se entrenará con datos reales del autor:

| Fuente | Variables | Período |
|---|---|---|
| Medtronic CareLink (CGM) | Glucosa cada 5 min, bolos de insulina con timestamp | 2022 → presente |
| Garmin Connect | Estrés diario, FC, sueño, pasos, calorías | 2022 → presente (~965 días) |
| Registro manual (Glucast) | Carbohidratos, ejercicio, notas clínicas | 2025 → presente |

---

## 🏥 Infraestructura

| Servicio | Plataforma |
|---|---|
| Nightscout | Railway (plan Hobby) |
| MongoDB | Railway (plan Hobby) |
| Código fuente | GitHub (este repo) |
| Entorno de desarrollo | GitHub Codespaces |
| Prototipo móvil | Expo Snack |

---

## 📄 Licencia

Este proyecto es de uso académico y no comercial. Todos los datos utilizados son propiedad del autor.

---

*Glucast — "glucose + forecast" — TFM MUIA · UNIR · 2025-2026*

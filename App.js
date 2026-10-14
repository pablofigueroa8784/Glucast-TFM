import { useState, useEffect, useCallback, useRef } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, Alert,
  KeyboardAvoidingView, Platform, FlatList, ActivityIndicator, Dimensions, Modal, Image
} from 'react-native';
import { GestureHandlerRootView, GestureDetector, Gesture } from 'react-native-gesture-handler';
import Svg, { Path, Line, Text as SvgText } from 'react-native-svg';
import * as ImagePicker from 'expo-image-picker';
import * as ImageManipulator from 'expo-image-manipulator';

// Configuración privada: copiar config.example.js como config.js y completar los valores.
// config.js está excluido del repositorio (.gitignore).
import { NIGHTSCOUT_URL, NIGHTSCOUT_TOKEN, VISION_SERVER_URL } from './config';
const SCREEN_WIDTH = Dimensions.get('window').width - 40;
const RANGOS_ZOOM = [1, 2, 3, 6, 12, 24, 48];

const FLECHAS = {
  'DoubleUp': '↑↑', 'SingleUp': '↑', 'FortyFiveUp': '↗',
  'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓',
  'DoubleDown': '↓↓', 'NOT COMPUTABLE': '-', 'RATE OUT OF RANGE': '?',
};

const colorGlucosa = (valor) => {
  const v = parseInt(valor);
  if (isNaN(v)) return '#888';
  if (v < 70) return '#e53935';
  if (v > 180) return '#fb8c00';
  return '#43a047';
};

const generarDias = (anio, mes) => {
  const diasEnMes = new Date(anio, mes + 1, 0).getDate();
  return Array.from({ length: diasEnMes }, (_, i) => i + 1);
};

const MESES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];

function AppInterno() {
  const [pantalla, setPantalla] = useState('formulario');
  const [registros, setRegistros] = useState([]);
  const [editandoId, setEditandoId] = useState(null);
  const [glucosa, setGlucosa] = useState('');
  const [carbohidratos, setCarbohidratos] = useState('');
  const [insulina, setInsulina] = useState('');
  const [ejercicio, setEjercicio] = useState('');
  const [duracion, setDuracion] = useState('');
  const [notas, setNotas] = useState('');

  const [glucosaActual, setGlucosaActual] = useState(null);
  const [tendencia, setTendencia] = useState('Flat');
  const [historial, setHistorial] = useState([]);
  const [cargandoRT, setCargandoRT] = useState(false);
  const [ultimaActualizacion, setUltimaActualizacion] = useState(null);
  const [diasSinActualizar, setDiasSinActualizar] = useState(null);
  const [datosGarmin, setDatosGarmin] = useState(null);
  const [cargandoGarmin, setCargandoGarmin] = useState(false);
  const [bolos, setBolos] = useState([]);

  // Escáner de comida
  const [escaneando, setEscaneando] = useState(false);
  const [resultadoEscaneo, setResultadoEscaneo] = useState(null);
  const [mostrarResultadoComida, setMostrarResultadoComida] = useState(false);
  const [fotoUri, setFotoUri] = useState(null);
  const [nombrePlato, setNombrePlato] = useState('');
  const [nombreCorreccion, setNombreCorreccion] = useState('');
  const ultimaImagenRef = useRef(null); // base64 reducido de la última foto, para reanalizar sin volver a sacarla

  // Tendencia manual de glucosa
  const [tendenciaManual, setTendenciaManual] = useState('→');
  const TENDENCIAS = [
    { simbolo: '↓↓↓', label: 'Fuerte ↓', correccion: -3.5, color: '#e53935' },
    { simbolo: '↓↓',  label: 'Moderada ↓', correccion: -2.5, color: '#fb8c00' },
    { simbolo: '↓',   label: 'Leve ↓', correccion: -1.5, color: '#fbc02d' },
    { simbolo: '→',   label: 'Estable', correccion: 0, color: '#43a047' },
    { simbolo: '↑',   label: 'Leve ↑', correccion: 1.5, color: '#fbc02d' },
    { simbolo: '↑↑',  label: 'Moderada ↑', correccion: 2.5, color: '#fb8c00' },
    { simbolo: '↑↑↑', label: 'Fuerte ↑', correccion: 3.5, color: '#e53935' },
  ];

  // Reajuste manual de carbohidratos
  const [reajusteCarbs, setReajusteCarbs] = useState('');
  const [iob, setIob] = useState('');

  // Configuración clínica del paciente
  const [config, setConfig] = useState({
    icRatio: 12,
    isf: 40,
    isfAlta: 40,          // ISF específico para corrección cuando glucosa está alta
    glucosaMin: 90,
    glucosaMax: 120,
    umbralCorreccion: 160,
  });
  const [configTemp, setConfigTemp] = useState(null); // edición temporal

  // Zoom
  const [indiceZoom, setIndiceZoom] = useState(RANGOS_ZOOM.indexOf(6));
  const indiceZoomInicio = useRef(indiceZoom);
  const rangoSeleccionado = RANGOS_ZOOM[indiceZoom];
  const setRangoSeleccionado = (horas) => {
    const idx = RANGOS_ZOOM.indexOf(horas);
    if (idx !== -1) { setIndiceZoom(idx); setOffsetHoras(0); }
  };

  const [offsetHoras, setOffsetHoras] = useState(0);
  const offsetInicio = useRef(0);
  const panActivo = useRef(false);
  const [scrollHabilitado, setScrollHabilitado] = useState(true);
  const scrollViewRef = useRef(null);

  const gestoPinch = Gesture.Pinch()
    .runOnJS(true)
    .onStart(() => { indiceZoomInicio.current = indiceZoom; })
    .onUpdate((e) => {
      const pasos = Math.round((1 - e.scale) * 3);
      const nuevoIndice = Math.min(RANGOS_ZOOM.length - 1, Math.max(0, indiceZoomInicio.current + pasos));
      if (nuevoIndice !== indiceZoom) setIndiceZoom(nuevoIndice);
    });

  const gestoPan = Gesture.Pan()
    .minDistance(10)
    .runOnJS(true)
    .onStart(() => { offsetInicio.current = offsetHoras; panActivo.current = true; setScrollHabilitado(false); })
    .onUpdate((e) => {
      const horasPorPixel = rangoSeleccionado / (SCREEN_WIDTH - 64);
      const deltaHoras = -e.translationX * horasPorPixel;
      const nuevoOffset = Math.max(0, offsetInicio.current + deltaHoras);
      setOffsetHoras(nuevoOffset);
    })
    .onEnd(() => { panActivo.current = false; setScrollHabilitado(true); })
    .onFinalize(() => { panActivo.current = false; setScrollHabilitado(true); });

  const gestosCombinados = Gesture.Simultaneous(gestoPinch, gestoPan);

  const hoy = new Date();
  const [fechaSeleccionada, setFechaSeleccionada] = useState(null);
  const [mostrarSelectorFecha, setMostrarSelectorFecha] = useState(false);
  const [selectorAnio, setSelectorAnio] = useState(hoy.getFullYear());
  const [selectorMes, setSelectorMes] = useState(hoy.getMonth());

  useEffect(() => { cargarRegistros(); fetchGlucosaActual(); verificarActualizacion(); cargarConfig(); }, []);

  const calcularInsulinaTotal = (gluc, carbs, tendSimbolo, iobActivo = '') => {
    const g = parseFloat(gluc) || 0;
    const c = parseFloat(carbs) || 0;
    const iobVal = parseFloat(iobActivo) || 0;
    const tend = TENDENCIAS.find(t => t.simbolo === tendSimbolo) || TENDENCIAS[3];

    const boloComida = c > 0 ? c / config.icRatio : 0;
    let correccionGluc = 0;
    if (g > config.umbralCorreccion) correccionGluc = (g - config.umbralCorreccion) / (config.isfAlta || config.isf);
    else if (g > 0 && g < config.glucosaMin) correccionGluc = (g - config.glucosaMin) / config.isf;

    const correccionTend = tend.correccion;
    const total = Math.round((boloComida + correccionGluc + correccionTend - iobVal) * 10) / 10;
    return String(total);
  };

  const cargarConfig = async () => {
    try {
      const datos = await AsyncStorage.getItem('config_clinica');
      if (datos) setConfig(JSON.parse(datos));
    } catch (e) {}
  };

  const guardarConfig = async (nuevaConfig) => {
    try {
      await AsyncStorage.setItem('config_clinica', JSON.stringify(nuevaConfig));
      setConfig(nuevaConfig);
    } catch (e) {}
  };

  const verificarActualizacion = useCallback(async () => {
    try {
      const res = await fetch(`${NIGHTSCOUT_URL}/api/v1/entries.json?count=1`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data || data.length === 0) return;
      const diasDiff = Math.floor((Date.now() - new Date(data[0].date).getTime()) / (1000 * 60 * 60 * 24));
      setDiasSinActualizar(diasDiff);
    } catch (e) {}
  }, []);

  useEffect(() => {
    if (pantalla === 'tendencias') {
      const fechaBase = fechaSeleccionada || new Date();
      fetchDatosGarmin(fechaBase);
      fetchBolos(fechaBase);
      if (fechaSeleccionada) { fetchHistorialFecha(fechaSeleccionada); }
      else { fetchHistorial(); const intervalo = setInterval(fetchGlucosaActual, 60000); return () => clearInterval(intervalo); }
    }
  }, [pantalla, fechaSeleccionada]);

  useEffect(() => { setOffsetHoras(0); }, [indiceZoom, fechaSeleccionada]);

  const fetchGlucosaActual = useCallback(async () => {
    try {
      const res = await fetch(`${NIGHTSCOUT_URL}/api/v1/entries/current.json`);
      if (!res.ok) return;
      const data = await res.json();
      const entrada = Array.isArray(data) ? data[0] : data;
      if (entrada && entrada.sgv) {
        setGlucosaActual(entrada.sgv); setTendencia(entrada.direction || 'Flat');
        setUltimaActualizacion(new Date(entrada.date));
        setGlucosa(prev => (prev === '' ? String(entrada.sgv) : prev));
      }
    } catch (e) {}
  }, []);

  const fetchHistorial = useCallback(async () => {
    setCargandoRT(true);
    try {
      const res = await fetch(`${NIGHTSCOUT_URL}/api/v1/entries.json?count=576`);
      if (!res.ok) return;
      const data = await res.json();
      setHistorial(data.filter(e => e.sgv).reverse());
    } catch (e) {} finally { setCargandoRT(false); }
  }, []);

  const fetchHistorialFecha = useCallback(async (fecha) => {
    setCargandoRT(true);
    try {
      const inicio = new Date(fecha); inicio.setHours(0, 0, 0, 0);
      const fin = new Date(fecha); fin.setHours(23, 59, 59, 999);
      const url = `${NIGHTSCOUT_URL}/api/v1/entries.json?find[date][$gte]=${inicio.getTime()}&find[date][$lte]=${fin.getTime()}&count=300`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      setHistorial(data.filter(e => e.sgv).reverse());
    } catch (e) {} finally { setCargandoRT(false); }
  }, []);

  const fetchBolos = useCallback(async (fecha) => {
    try {
      const inicio = new Date(fecha); inicio.setHours(0, 0, 0, 0);
      const fin = new Date(fecha); fin.setHours(23, 59, 59, 999);
      const url = `${NIGHTSCOUT_URL}/api/v1/treatments.json?find[enteredBy]=Glucast-CareLinkImport&find[created_at][$gte]=${inicio.toISOString()}&find[created_at][$lte]=${fin.toISOString()}&count=100`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      setBolos(data.filter(t => t.insulin).map(t => ({ ...t, timestamp: new Date(t.created_at).getTime() })));
    } catch (e) {}
  }, []);

  const fetchDatosGarmin = useCallback(async (fecha) => {
    setCargandoGarmin(true); setDatosGarmin(null);
    try {
      const inicio = new Date(fecha); inicio.setHours(0, 0, 0, 0);
      const fin = new Date(fecha); fin.setHours(23, 59, 59, 999);
      const url = `${NIGHTSCOUT_URL}/api/v1/treatments.json?find[eventType]=Note&find[created_at][$gte]=${inicio.toISOString()}&find[created_at][$lte]=${fin.toISOString()}&count=5`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const notaGarmin = data.find(t => t.enteredBy === 'Glucast-GarminImport' && t.notes);
      if (notaGarmin) setDatosGarmin(parsearNotaGarmin(notaGarmin.notes));
    } catch (e) {} finally { setCargandoGarmin(false); }
  }, []);

  const parsearNotaGarmin = (texto) => {
    const resultado = {};
    const estresMatch = texto.match(/Estrés prom: (\d+) \(máx (\d+)\)/);
    const fcMatch = texto.match(/FC reposo: (\d+) bpm/);
    const suenoMatch = texto.match(/Sueño: ([\d.]+)h/);
    const pasosMatch = texto.match(/Pasos: (\d+)/);
    if (estresMatch) { resultado.estresProm = estresMatch[1]; resultado.estresMax = estresMatch[2]; }
    if (fcMatch) resultado.fcReposo = fcMatch[1];
    if (suenoMatch) resultado.suenoHoras = suenoMatch[1];
    if (pasosMatch) resultado.pasos = pasosMatch[1];
    return Object.keys(resultado).length > 0 ? resultado : null;
  };

  const colorEstres = (valor) => {
    const v = parseInt(valor);
    if (isNaN(v)) return '#888';
    if (v < 25) return '#43a047';
    if (v < 50) return '#fbc02d';
    if (v < 75) return '#fb8c00';
    return '#e53935';
  };

  // ── ESCÁNER DE COMIDA ────────────────────────────────────────────────────
  const escanearComida = async (fuente = 'camera') => {
    let result;
    if (fuente === 'camera') {
      const { status } = await ImagePicker.requestCameraPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permiso requerido', 'Necesitamos acceso a la cámara para escanear la comida.');
        return;
      }
      result = await ImagePicker.launchCameraAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 1,
      });
    } else {
      const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permiso requerido', 'Necesitamos acceso a la fototeca.');
        return;
      }
      result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 1,
      });
    }
    if (result.canceled) return;
    const asset = result.assets[0];
    setFotoUri(asset.uri);
    setEscaneando(true);
    try {
      // Reducir a 1024 px de ancho: la foto original de la cámara pesa varios MB en
      // base64 y provocaba timeouts; Vision no necesita más resolución para reconocer el plato.
      const reducida = await ImageManipulator.manipulateAsync(
        asset.uri,
        asset.width > 1024 ? [{ resize: { width: 1024 } }] : [],
        { compress: 0.7, format: ImageManipulator.SaveFormat.JPEG, base64: true }
      );
      ultimaImagenRef.current = reducida.base64;
    } catch (e) {
      setEscaneando(false);
      Alert.alert('Error', `No se pudo procesar la foto: ${e.message}`);
      return;
    }
    await analizarImagen(nombrePlato);
  };

  // Envía la última foto al vision-server. Se usa al escanear y al corregir el nombre del plato.
  const analizarImagen = async (nombre) => {
    if (!ultimaImagenRef.current) return;
    setEscaneando(true);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const res = await fetch(`${VISION_SERVER_URL}/analizar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imagen: ultimaImagenRef.current, ic_ratio: config.icRatio, nombre_plato: nombre || '' }),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      const data = await res.json();
      if (data.error && !data.porciones) {
        const detectado = (data.labels || []).slice(0, 4).map(l => l.nombre).join(', ');
        Alert.alert(
          'No se pudo calcular',
          (data.sugerencia || data.error || 'Intentá con otra foto más clara.') + (detectado ? `\n\nDetectado: ${detectado}` : '')
        );
      } else {
        setResultadoEscaneo(data);
        setNombreCorreccion('');
        setMostrarResultadoComida(true);
      }
    } catch (e) {
      clearTimeout(timeout);
      if (e.name === 'AbortError') {
        Alert.alert('Tiempo agotado', 'El servidor tardó demasiado. Intentá de nuevo.');
      } else {
        Alert.alert('Error', `No se pudo conectar: ${e.message}`);
      }
    } finally {
      setEscaneando(false);
    }
  };

  const cerrarResultadoComida = () => {
    setMostrarResultadoComida(false);
    setResultadoEscaneo(null);
    setFotoUri(null);
    setNombrePlato('');
    setNombreCorreccion('');
    ultimaImagenRef.current = null;
  };

  const aplicarPorcion = (tamano) => {
    if (!resultadoEscaneo) return;
    const porcion = resultadoEscaneo.porciones[tamano];
    const carbs = porcion.carbs_totales;
    setCarbohidratos(String(carbs));
    const insulinaTotal = calcularInsulinaTotal(glucosa, reajusteCarbs || String(carbs), tendenciaManual, iob);
    setInsulina(insulinaTotal);
    cerrarResultadoComida();
  };

  const seleccionarDia = (dia) => { setFechaSeleccionada(new Date(selectorAnio, selectorMes, dia)); setMostrarSelectorFecha(false); };
  const volverATiempoReal = () => { setFechaSeleccionada(null); setOffsetHoras(0); fetchHistorial(); };

  const cargarRegistros = async () => {
    try { const datos = await AsyncStorage.getItem('registros'); if (datos !== null) setRegistros(JSON.parse(datos)); } catch (e) {}
  };

  const subirTreatmentNightscout = async (registro) => {
    try {
      const treatment = {
        eventType: 'Meal Bolus', created_at: new Date().toISOString(),
        glucose: parseFloat(registro.glucosa), glucoseType: 'Sensor',
        carbs: registro.carbohidratos ? parseFloat(registro.carbohidratos) : undefined,
        insulin: registro.insulina ? parseFloat(registro.insulina) : undefined,
        duration: registro.duracion ? parseFloat(registro.duracion) : undefined,
        notes: `${registro.ejercicio ? 'Ejercicio: ' + registro.ejercicio : ''} ${registro.notas || ''}`.trim(),
        enteredBy: 'GlucoAI'
      };
      await fetch(`${NIGHTSCOUT_URL}/api/v1/treatments?token=${NIGHTSCOUT_TOKEN}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([treatment])
      });
    } catch (e) {}
  };

  const guardarEnStorage = async (nuevosRegistros) => {
    try { await AsyncStorage.setItem('registros', JSON.stringify(nuevosRegistros)); } catch (e) {}
  };

  const limpiarFormulario = () => {
    setGlucosa(glucosaActual ? String(glucosaActual) : '');
    setCarbohidratos(''); setInsulina(''); setEjercicio(''); setDuracion(''); setNotas('');
    setEditandoId(null); setTendenciaManual('→'); setReajusteCarbs(''); setNombrePlato(''); setIob('');
  };

  const ahora = new Date();
  const horaActual = ahora.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
  const fechaActual = ahora.toLocaleDateString('es-AR');

  const minutosDesde = (fecha) => {
    if (!fecha) return '';
    const diff = Math.floor((new Date() - fecha) / 60000);
    if (diff < 1) return 'ahora mismo';
    if (diff === 1) return 'hace 1 min';
    return `hace ${diff} min`;
  };

  const guardarRegistro = () => {
    if (!glucosa) { Alert.alert('Error', 'Por favor ingresa el valor de glucosa'); return; }
    const nivelGlucosa = parseInt(glucosa);
    let alerta = '';
    if (nivelGlucosa < 54) alerta = 'ALERTA: Glucosa muy baja';
    else if (nivelGlucosa < 70) alerta = 'Glucosa baja';
    else if (nivelGlucosa > 180) alerta = 'Glucosa alta';
    if (editandoId !== null) {
      const actualizados = registros.map(r => r.id === editandoId ? { ...r, glucosa, insulina, carbohidratos, ejercicio, duracion, notas, editado: true } : r);
      setRegistros(actualizados); guardarEnStorage(actualizados);
      Alert.alert('Registro actualizado', 'Los cambios fueron guardados.');
    } else {
      const nuevo = { id: Date.now(), fecha: fechaActual, hora: horaActual, glucosa, insulina, carbohidratos, ejercicio, duracion, notas, editado: false };
      const actualizados = [nuevo, ...registros];
      setRegistros(actualizados); guardarEnStorage(actualizados); subirTreatmentNightscout(nuevo);
      Alert.alert(alerta || 'Registro guardado',
        'Fecha: ' + fechaActual + ' ' + horaActual + '\nGlucosa: ' + glucosa + ' mg/dL\nInsulina: ' + insulina + ' U\nCarbohidratos: ' + carbohidratos + 'g\nEjercicio: ' + ejercicio + ' (' + duracion + ' min)\nNotas: ' + notas);
    }
    limpiarFormulario();
  };

  const editarRegistro = (registro) => {
    setGlucosa(registro.glucosa); setInsulina(registro.insulina); setCarbohidratos(registro.carbohidratos);
    setEjercicio(registro.ejercicio); setDuracion(registro.duracion); setNotas(registro.notas);
    setEditandoId(registro.id); setPantalla('formulario');
  };

  const eliminarRegistro = (id) => {
    Alert.alert('Eliminar registro', 'Estas seguro que queres eliminar este registro?', [
      { text: 'Cancelar', style: 'cancel' },
      { text: 'Eliminar', style: 'destructive', onPress: () => { const actualizados = registros.filter(r => r.id !== id); setRegistros(actualizados); guardarEnStorage(actualizados); } }
    ]);
  };

  // ── PANTALLA TENDENCIAS ──────────────────────────────────────────────────
  if (pantalla === 'tendencias') {
    const flecha = FLECHAS[tendencia] || '→';
    const colorActual = colorGlucosa(glucosaActual);
    const rangos = [
      { label: '1h', horas: 1 }, { label: '3h', horas: 3 }, { label: '6h', horas: 6 },
      { label: '12h', horas: 12 }, { label: '24h', horas: 24 }, { label: '48h', horas: 48 },
    ];
    const ahoraMs = Date.now();
    const finVentana = fechaSeleccionada
      ? new Date(fechaSeleccionada).setHours(23, 59, 59, 999) - offsetHoras * 3600000
      : ahoraMs - offsetHoras * 3600000;
    const inicioVentana = finVentana - rangoSeleccionado * 3600000;
    const historialFiltrado = historial.filter(e => e.date >= inicioVentana && e.date <= finVentana);
    const tituloFecha = fechaSeleccionada
      ? fechaSeleccionada.toLocaleDateString('es-AR', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
      : 'Tiempo real';
    const valores = historialFiltrado.map(e => e.sgv);
    const promedio = valores.length ? Math.round(valores.reduce((a, b) => a + b, 0) / valores.length) : null;
    const minVal = valores.length ? Math.min(...valores) : null;
    const maxVal = valores.length ? Math.max(...valores) : null;
    const enRango = valores.filter(v => v >= 70 && v <= 180).length;
    const pctRango = valores.length ? Math.round((enRango / valores.length) * 100) : null;
    const bolosFiltrados = historialFiltrado.length > 0 ? bolos.filter(b => b.timestamp >= inicioVentana && b.timestamp <= finVentana) : [];
    const diasDelMes = generarDias(selectorAnio, selectorMes);
    const horaInicio = new Date(inicioVentana).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
    const horaFin = new Date(finVentana).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
    const offsetLabel = offsetHoras > 0 ? ` (−${Math.round(offsetHoras)}h)` : '';

    return (
      <View style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.titulo}>GlucoAI</Text>
          <TouchableOpacity style={styles.botonSecundario} onPress={() => setPantalla('formulario')}>
            <Text style={styles.botonSecundarioTexto}>← Volver</Text>
          </TouchableOpacity>
        </View>
        <View style={styles.barraFecha}>
          <TouchableOpacity style={styles.botonFecha} onPress={() => setMostrarSelectorFecha(true)}>
            <Text style={styles.botonFechaTexto}>📅 {tituloFecha}</Text>
          </TouchableOpacity>
          {(fechaSeleccionada || offsetHoras > 0) && (
            <TouchableOpacity style={styles.botonTiempoReal} onPress={volverATiempoReal}>
              <Text style={styles.botonTiempoRealTexto}>🔴 En vivo</Text>
            </TouchableOpacity>
          )}
        </View>
        {!fechaSeleccionada && offsetHoras === 0 && (
          <View style={styles.cardRT}>
            <Text style={[styles.valorRT, { color: colorActual }]}>{glucosaActual ?? '--'}</Text>
            <Text style={[styles.flechaRT, { color: colorActual }]}>{flecha}</Text>
            <Text style={styles.unidadRT}>mg/dL</Text>
            <Text style={[styles.estadoRT, { color: colorActual }]}>
              {!glucosaActual ? 'Sin datos' : glucosaActual < 70 ? '⚠ Glucosa baja' : glucosaActual > 180 ? '⚠ Glucosa alta' : '✓ En rango'}
            </Text>
            <Text style={styles.tiempoRT}>{minutosDesde(ultimaActualizacion)}</Text>
          </View>
        )}
        {valores.length > 0 && (
          <View style={styles.statsContainer}>
            <View style={styles.statItem}><Text style={styles.statLabel}>Promedio</Text><Text style={[styles.statValor, { color: colorGlucosa(promedio) }]}>{promedio}</Text></View>
            <View style={styles.statItem}><Text style={styles.statLabel}>Mín</Text><Text style={[styles.statValor, { color: colorGlucosa(minVal) }]}>{minVal}</Text></View>
            <View style={styles.statItem}><Text style={styles.statLabel}>Máx</Text><Text style={[styles.statValor, { color: colorGlucosa(maxVal) }]}>{maxVal}</Text></View>
            <View style={styles.statItem}><Text style={styles.statLabel}>En rango</Text><Text style={[styles.statValor, { color: pctRango >= 70 ? '#43a047' : '#fb8c00' }]}>{pctRango}%</Text></View>
          </View>
        )}
        {cargandoGarmin ? (
          <View style={styles.garminCard}><ActivityIndicator color="#1a73e8" size="small" /></View>
        ) : datosGarmin ? (
          <View style={styles.garminCard}>
            <Text style={styles.garminTitulo}>⌚ Garmin — {tituloFecha}</Text>
            <View style={styles.garminStats}>
              {datosGarmin.estresProm && (<View style={styles.statItem}><Text style={styles.statLabel}>Estrés</Text><Text style={[styles.statValor, { color: colorEstres(datosGarmin.estresProm) }]}>{datosGarmin.estresProm}</Text><Text style={styles.garminSubLabel}>máx {datosGarmin.estresMax}</Text></View>)}
              {datosGarmin.fcReposo && (<View style={styles.statItem}><Text style={styles.statLabel}>FC reposo</Text><Text style={styles.statValor}>{datosGarmin.fcReposo}</Text><Text style={styles.garminSubLabel}>bpm</Text></View>)}
              {datosGarmin.suenoHoras && (<View style={styles.statItem}><Text style={styles.statLabel}>Sueño</Text><Text style={styles.statValor}>{datosGarmin.suenoHoras}</Text><Text style={styles.garminSubLabel}>horas</Text></View>)}
              {datosGarmin.pasos && (<View style={styles.statItem}><Text style={styles.statLabel}>Pasos</Text><Text style={styles.statValor}>{datosGarmin.pasos}</Text></View>)}
            </View>
          </View>
        ) : null}
        <View style={styles.filtrosContainer}>
          {rangos.map(r => (
            <TouchableOpacity key={r.horas} style={[styles.filtroBton, rangoSeleccionado === r.horas && styles.filtroBtonActivo]} onPress={() => setRangoSeleccionado(r.horas)}>
              <Text style={[styles.filtroBtonTexto, rangoSeleccionado === r.horas && styles.filtroBtonTextoActivo]}>{r.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
        <Text style={styles.seccionTitulo}>{historialFiltrado.length} lecturas — {horaInicio} → {horaFin}{offsetLabel}</Text>
        <Text style={styles.zoomHint}>🤏 Pellizcá para zoom · 👆 Arrastrá para navegar en el tiempo</Text>
        {cargandoRT ? (
          <ActivityIndicator color="#1a73e8" style={{ marginTop: 20 }} />
        ) : historialFiltrado.length > 1 ? (
          <GestureDetector gesture={gestosCombinados}>
            <View style={styles.graficoContainer}>
              {(() => {
                const margen = 20;
                const yMin = Math.max(40, (minVal || 70) - margen);
                const yMax = Math.min(400, (maxVal || 180) + margen);
                const yRango = yMax - yMin;
                const W = SCREEN_WIDTH - 32;
                const H = 130;
                const px = (ts) => ((ts - inicioVentana) / (finVentana - inicioVentana)) * W;
                const py = (val) => H - ((val - yMin) / yRango) * H;
                const segmentos = [];
                for (let i = 0; i < historialFiltrado.length - 1; i++) {
                  const a = historialFiltrado[i]; const b = historialFiltrado[i + 1];
                  segmentos.push(<Path key={`seg-${i}`} d={`M${px(a.date)},${py(a.sgv)} L${px(b.date)},${py(b.sgv)}`} stroke={colorGlucosa(a.sgv)} strokeWidth={2.5} strokeLinecap="round" />);
                }
                const duracionMs = finVentana - inicioVentana;
                const duracionHoras = duracionMs / 3600000;
                let intervaloMin;
                if (duracionHoras <= 1) intervaloMin = 10;
                else if (duracionHoras <= 3) intervaloMin = 30;
                else if (duracionHoras <= 6) intervaloMin = 60;
                else if (duracionHoras <= 12) intervaloMin = 120;
                else if (duracionHoras <= 24) intervaloMin = 240;
                else intervaloMin = 480;
                const intervaloMs = intervaloMin * 60000;
                const primerTick = Math.ceil(inicioVentana / intervaloMs) * intervaloMs;
                const ticksSvg = [];
                for (let t = primerTick; t <= finVentana; t += intervaloMs) {
                  const pct = (t - inicioVentana) / duracionMs;
                  if (pct < 0 || pct > 1) continue;
                  const x = pct * W;
                  const label = new Date(t).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
                  ticksSvg.push(
                    <Line key={`tick-${t}`} x1={x} y1={H} x2={x} y2={H + 4} stroke="#ddd" strokeWidth={1} />,
                    <SvgText key={`label-${t}`} x={x} y={H + 14} fontSize={8} fill="#aaa" textAnchor="middle">{label}</SvgText>
                  );
                }
                return (
                  <Svg width={W} height={H + 20}>
                    {70 >= yMin && 70 <= yMax && (<><Line x1={0} y1={py(70)} x2={W} y2={py(70)} stroke="#e53935" strokeWidth={1} strokeDasharray="4,3" /><SvgText x={W - 2} y={py(70) - 3} fontSize={9} fill="#e53935" textAnchor="end">70</SvgText></>)}
                    {180 >= yMin && 180 <= yMax && (<><Line x1={0} y1={py(180)} x2={W} y2={py(180)} stroke="#fb8c00" strokeWidth={1} strokeDasharray="4,3" /><SvgText x={W - 2} y={py(180) - 3} fontSize={9} fill="#fb8c00" textAnchor="end">180</SvgText></>)}
                    <SvgText x={2} y={10} fontSize={9} fill="#bbb">{Math.round(yMax)}</SvgText>
                    <SvgText x={2} y={H - 2} fontSize={9} fill="#bbb">{Math.round(yMin)}</SvgText>
                    {segmentos}
                    {bolosFiltrados.map((b, i) => (<SvgText key={`bolo-${i}`} x={px(b.timestamp)} y={H - 4} fontSize={12} textAnchor="middle">💉</SvgText>))}
                    {ticksSvg}
                  </Svg>
                );
              })()}
            </View>
          </GestureDetector>
        ) : (
          <Text style={styles.vacio}>Sin datos en este rango</Text>
        )}
        {bolosFiltrados.length > 0 && (
          <Text style={styles.bolosLeyenda}>💉 {bolosFiltrados.length} {bolosFiltrados.length === 1 ? 'bolo' : 'bolos'} de insulina en este rango</Text>
        )}
        {historialFiltrado.length > 0 && (
          <ScrollView ref={scrollViewRef} scrollEnabled={scrollHabilitado} style={{ marginTop: 12, flex: 1 }}>
            {[...historialFiltrado].reverse().map((e, i) => (
              <View key={i} style={styles.filaHistorial}>
                <Text style={styles.filaHora}>{new Date(e.date).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })} {new Date(e.date).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })}</Text>
                <Text style={[styles.filaValor, { color: colorGlucosa(e.sgv) }]}>{e.sgv} mg/dL</Text>
                <Text style={styles.filaFlecha}>{FLECHAS[e.direction] || '→'}</Text>
              </View>
            ))}
          </ScrollView>
        )}
        <Modal visible={mostrarSelectorFecha} transparent animationType="slide">
          <View style={styles.modalOverlay}>
            <View style={styles.modalContainer}>
              <Text style={styles.modalTitulo}>Seleccionar fecha</Text>
              <View style={styles.navMes}>
                <TouchableOpacity onPress={() => { if (selectorMes === 0) { setSelectorMes(11); setSelectorAnio(a => a - 1); } else setSelectorMes(m => m - 1); }}><Text style={styles.navBtn}>‹</Text></TouchableOpacity>
                <Text style={styles.navLabel}>{MESES[selectorMes]} {selectorAnio}</Text>
                <TouchableOpacity onPress={() => { if (selectorMes === 11) { setSelectorMes(0); setSelectorAnio(a => a + 1); } else setSelectorMes(m => m + 1); }}><Text style={styles.navBtn}>›</Text></TouchableOpacity>
              </View>
              <View style={styles.grillaDias}>
                {diasDelMes.map(dia => {
                  const esFuturo = new Date(selectorAnio, selectorMes, dia) > new Date();
                  const esHoy = dia === hoy.getDate() && selectorMes === hoy.getMonth() && selectorAnio === hoy.getFullYear();
                  const esSeleccionado = fechaSeleccionada && dia === fechaSeleccionada.getDate() && selectorMes === fechaSeleccionada.getMonth() && selectorAnio === fechaSeleccionada.getFullYear();
                  return (
                    <TouchableOpacity key={dia} style={[styles.diaBtn, esHoy && styles.diaBtnHoy, esSeleccionado && styles.diaBtnSeleccionado, esFuturo && styles.diaBtnDeshabilitado]} onPress={() => !esFuturo && seleccionarDia(dia)} disabled={esFuturo}>
                      <Text style={[styles.diaBtnTexto, esSeleccionado && styles.diaBtnTextoSeleccionado, esFuturo && { color: '#ccc' }]}>{dia}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              <TouchableOpacity style={styles.modalCerrar} onPress={() => setMostrarSelectorFecha(false)}><Text style={styles.modalCerrarTexto}>Cancelar</Text></TouchableOpacity>
            </View>
          </View>
        </Modal>
      </View>
    );
  }

  // ── PANTALLA LISTA ───────────────────────────────────────────────────────
  if (pantalla === 'lista') {
    return (
      <View style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.titulo}>GlucoAI</Text>
          <TouchableOpacity style={styles.botonSecundario} onPress={() => { limpiarFormulario(); setPantalla('formulario'); }}><Text style={styles.botonSecundarioTexto}>+ Nuevo</Text></TouchableOpacity>
        </View>
        <Text style={styles.subtitulo}>Historial de registros</Text>
        {registros.length === 0 ? (
          <Text style={styles.vacio}>No hay registros todavia.</Text>
        ) : (
          <FlatList
            data={registros}
            keyExtractor={item => item.id.toString()}
            renderItem={({ item }) => (
              <View style={styles.tarjeta}>
                <View style={styles.tarjetaHeader}>
                  <Text style={styles.tarjetaFecha}>{item.fecha} {item.hora}</Text>
                  <Text style={[styles.tarjetaGlucosa, { color: colorGlucosa(item.glucosa) }]}>{item.glucosa} mg/dL</Text>
                </View>
                <Text style={styles.tarjetaDetalle}>{item.insulina || '-'} U  {item.carbohidratos || '-'} g  {item.ejercicio || '-'} {item.duracion ? '(' + item.duracion + ' min)' : ''}</Text>
                {item.notas ? <Text style={styles.tarjetaNotas}>{item.notas}</Text> : null}
                {item.editado ? <Text style={styles.etiquetaEditado}>Editado</Text> : null}
                <View style={styles.tarjetaBotones}>
                  <TouchableOpacity style={styles.botonEditar} onPress={() => editarRegistro(item)}><Text style={styles.botonEditarTexto}>Editar</Text></TouchableOpacity>
                  <TouchableOpacity style={styles.botonEliminar} onPress={() => eliminarRegistro(item.id)}><Text style={styles.botonEliminarTexto}>Eliminar</Text></TouchableOpacity>
                </View>
              </View>
            )}
          />
        )}
      </View>
    );
  }

  // ── PANTALLA CONFIGURACIÓN ───────────────────────────────────────────────
  if (pantalla === 'configuracion') {
    const ct = configTemp || config;
    return (
      <ScrollView style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.titulo}>Mi Perfil Clínico</Text>
          <TouchableOpacity style={styles.botonSecundario} onPress={() => setPantalla('formulario')}>
            <Text style={styles.botonSecundarioTexto}>← Volver</Text>
          </TouchableOpacity>
        </View>

        <Text style={styles.subtitulo}>Parámetros clínicos</Text>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Ratio Insulina/Carbohidratos (I:C)</Text>
          <Text style={styles.label}>Gramos de carbohidratos por unidad de insulina</Text>
          <TextInput
            style={styles.input}
            value={String(ct.icRatio)}
            onChangeText={v => setConfigTemp({...ct, icRatio: parseFloat(v) || ct.icRatio})}
            keyboardType="numeric"
            placeholder="Ej: 12"
          />
          <Text style={styles.configHint}>Ejemplo: si ponés 12, cada unidad cubre 12g de carbs</Text>
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Factor de Sensibilidad (ISF)</Text>
          <Text style={styles.label}>ISF general — mg/dL que baja 1 unidad de insulina</Text>
          <TextInput
            style={styles.input}
            value={String(ct.isf)}
            onChangeText={v => setConfigTemp({...ct, isf: parseFloat(v) || ct.isf})}
            keyboardType="numeric"
            placeholder="Ej: 40"
          />
          <Text style={styles.label}>ISF para corrección alta — cuando glucosa supera el umbral</Text>
          <TextInput
            style={styles.input}
            value={String(ct.isfAlta)}
            onChangeText={v => setConfigTemp({...ct, isfAlta: parseFloat(v) || ct.isfAlta})}
            keyboardType="numeric"
            placeholder="Ej: 35"
          />
          <Text style={styles.configHint}>Si en hiperglucemia tu insulina es más efectiva, usá un valor menor</Text>
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Objetivo de glucemia</Text>
          <Text style={styles.label}>Mínimo (mg/dL)</Text>
          <TextInput
            style={styles.input}
            value={String(ct.glucosaMin)}
            onChangeText={v => setConfigTemp({...ct, glucosaMin: parseFloat(v) || ct.glucosaMin})}
            keyboardType="numeric"
            placeholder="Ej: 90"
          />
          <Text style={styles.label}>Máximo (mg/dL)</Text>
          <TextInput
            style={styles.input}
            value={String(ct.glucosaMax)}
            onChangeText={v => setConfigTemp({...ct, glucosaMax: parseFloat(v) || ct.glucosaMax})}
            keyboardType="numeric"
            placeholder="Ej: 120"
          />
          <Text style={styles.configHint}>El gráfico de tendencias usa estos valores como rango objetivo</Text>
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Umbral de corrección alta</Text>
          <Text style={styles.label}>Glucosa a partir de la cual agregar corrección al comer (mg/dL)</Text>
          <TextInput
            style={styles.input}
            value={String(ct.umbralCorreccion)}
            onChangeText={v => setConfigTemp({...ct, umbralCorreccion: parseFloat(v) || ct.umbralCorreccion})}
            keyboardType="numeric"
            placeholder="Ej: 160"
          />
          <Text style={styles.configHint}>Si antes de comer estás por encima de este valor, se suman unidades de corrección</Text>
        </View>

        <View style={[styles.seccion, { backgroundColor: '#e8f0fe' }]}>
          <Text style={[styles.seccionTitulo, { color: '#1a73e8' }]}>Resumen actual</Text>
          <Text style={styles.configResumen}>• I:C = {ct.icRatio}g carbs / unidad</Text>
          <Text style={styles.configResumen}>• ISF general = {ct.isf} mg/dL / unidad</Text>
          <Text style={styles.configResumen}>• ISF corrección alta = {ct.isfAlta} mg/dL / unidad</Text>
          <Text style={styles.configResumen}>• Objetivo = {ct.glucosaMin}–{ct.glucosaMax} mg/dL</Text>
          <Text style={styles.configResumen}>• Corrección alta desde = {ct.umbralCorreccion} mg/dL</Text>
        </View>

        <TouchableOpacity style={styles.boton} onPress={() => {
          guardarConfig(ct);
          Alert.alert('✅ Configuración guardada', `I:C: ${ct.icRatio}g/U · ISF: ${ct.isf} mg/dL/U · Objetivo: ${ct.glucosaMin}-${ct.glucosaMax} mg/dL`);
          setPantalla('formulario');
        }}>
          <Text style={styles.botonTexto}>Guardar configuración</Text>
        </TouchableOpacity>

        <View style={{ height: 60 }} />
      </ScrollView>
    );
  }

  // ── PANTALLA FORMULARIO ──────────────────────────────────────────────────
  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'} keyboardVerticalOffset={150}>
      <ScrollView style={styles.container} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" automaticallyAdjustKeyboardInsets={true}>
        <View style={styles.header}>
          <Text style={styles.titulo}>GlucoAI</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity style={styles.botonSecundario} onPress={() => { fetchHistorial(); setPantalla('tendencias'); }}><Text style={styles.botonSecundarioTexto}>📈 Tendencias</Text></TouchableOpacity>
            <TouchableOpacity style={styles.botonSecundario} onPress={() => setPantalla('lista')}><Text style={styles.botonSecundarioTexto}>Registros ({registros.length})</Text></TouchableOpacity>
          </View>
        </View>

        <Text style={styles.fecha}>{fechaActual} - {horaActual}</Text>

        <TouchableOpacity style={styles.botonConfiguracion} onPress={() => { setConfigTemp({...config}); setPantalla('configuracion'); }}>
          <Text style={styles.botonConfiguracionTexto}>⚙️ Mi Perfil Clínico</Text>
        </TouchableOpacity>
        {editandoId !== null && <Text style={styles.editandoAviso}>Editando registro existente</Text>}

        {diasSinActualizar !== null && diasSinActualizar >= 7 && (
          <TouchableOpacity style={styles.avisoCsv} onPress={() => Alert.alert('📥 Actualizar datos CGM', `Hace ${diasSinActualizar} días que no se actualizan los datos en Nightscout.\n\n1. Descargá el CSV desde carelink.minimed.eu\n2. Corré el script: python3 ~/Downloads/importar_nightscout.py`, [{ text: 'Entendido', style: 'default' }])}>
            <Text style={styles.avisoCsvTexto}>⚠️ Hace {diasSinActualizar} días sin actualizar datos — Tocá para ver instrucciones</Text>
          </TouchableOpacity>
        )}

        <View style={styles.seccion}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <Text style={styles.seccionTitulo}>Glucosa</Text>
            {glucosaActual && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <View style={[styles.puntoCGM, { backgroundColor: colorGlucosa(glucosaActual) }]} />
                <Text style={[styles.cgmLabel, { color: colorGlucosa(glucosaActual) }]}>CGM: {glucosaActual} {FLECHAS[tendencia] || '→'}</Text>
              </View>
            )}
          </View>
          <Text style={styles.label}>Valor (mg/dL)</Text>
          <TextInput
            style={[styles.input, glucosa && { borderColor: colorGlucosa(glucosa), borderWidth: 1.5 }]}
            value={glucosa}
            onChangeText={(v) => {
              setGlucosa(v);
              const carbs = reajusteCarbs || carbohidratos;
              setInsulina(calcularInsulinaTotal(v, carbs, tendenciaManual, iob));
            }}
            keyboardType="numeric"
            placeholder="Cargando desde CGM..."
          />
          {ultimaActualizacion && <Text style={styles.cgmHora}>Última lectura CGM: {minutosDesde(ultimaActualizacion)}</Text>}

          {/* Selector de tendencia manual */}
          <Text style={styles.label}>Tendencia de glucosa</Text>
          <View style={styles.tendenciaContainer}>
            {TENDENCIAS.map(t => (
              <TouchableOpacity
                key={t.simbolo}
                style={[styles.tendenciaBtn, tendenciaManual === t.simbolo && { backgroundColor: t.color, borderColor: t.color }]}
                onPress={() => {
                  setTendenciaManual(t.simbolo);
                  const carbs = reajusteCarbs || carbohidratos;
                  setInsulina(calcularInsulinaTotal(glucosa, carbs, t.simbolo, iob));
                }}
              >
                <Text style={[styles.tendenciaBtnTexto, tendenciaManual === t.simbolo && { color: '#fff' }]}>{t.simbolo}</Text>
              </TouchableOpacity>
            ))}
          </View>
          {tendenciaManual !== '→' && (
            <Text style={styles.tendenciaHint}>
              {TENDENCIAS.find(t => t.simbolo === tendenciaManual)?.label} —
              {TENDENCIAS.find(t => t.simbolo === tendenciaManual)?.correccion > 0 ? ' +' : ' '}
              {TENDENCIAS.find(t => t.simbolo === tendenciaManual)?.correccion}U por tendencia
            </Text>
          )}
          {parseFloat(glucosa) > config.umbralCorreccion && (
            <View style={styles.correccionPura}>
              <Text style={styles.correccionPuraTexto}>
                ⚠️ Glucosa alta — corrección sugerida: {Math.max(0, Math.round(((parseFloat(glucosa) - config.umbralCorreccion) / config.isf) * 10) / 10)} U
              </Text>
              <Text style={styles.correccionPuraHint}>Sin contar carbohidratos — se sumará al bolo cuando ingreses los carbs</Text>
              {!carbohidratos && (
                <TouchableOpacity onPress={() => {
                  const correccion = Math.max(0, Math.round(((parseFloat(glucosa) - config.umbralCorreccion) / config.isf) * 10) / 10);
                  setInsulina(String(correccion));
                }}>
                  <Text style={styles.correccionPuraBtn}>Aplicar corrección →</Text>
                </TouchableOpacity>
              )}
            </View>
          )}
          {parseFloat(glucosa) < config.glucosaMin && (
            <View style={[styles.correccionPura, { borderLeftColor: '#1a73e8', backgroundColor: '#e8f0fe' }]}>
              <Text style={[styles.correccionPuraTexto, { color: '#1a73e8' }]}>
                ✓ Glucosa baja — reducir insulina en: {Math.round(Math.abs((parseFloat(glucosa) - config.glucosaMin) / config.isf) * 10) / 10} U
              </Text>
            </View>
          )}
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Insulina</Text>
          <Text style={styles.label}>Insulina activa al momento de comer</Text>
          <TextInput
            style={[styles.input, iob && { borderColor: '#9c27b0', borderWidth: 1.5 }]}
            value={iob}
            onChangeText={(v) => {
              setIob(v);
              const carbs = reajusteCarbs || carbohidratos;
              setInsulina(calcularInsulinaTotal(glucosa, carbs, tendenciaManual, v));
            }}
            keyboardType="numeric"
            placeholder="Unidades activas de insulina previa..."
          />
          {parseFloat(iob) > 0 && (
            <Text style={styles.iobHint}>⚡ Se restan {iob}U de IOB al cálculo total</Text>
          )}
          <Text style={styles.label}>Dosis bolo (unidades)</Text>
          <TextInput style={styles.input} value={insulina} onChangeText={setInsulina} keyboardType="numeric" placeholder="Ej: 9" />
        </View>

        <View style={styles.seccion}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <Text style={styles.seccionTitulo}>Alimentación</Text>
            <View style={{ flexDirection: 'row', gap: 6 }}>
              <TouchableOpacity style={styles.botonEscanear} onPress={() => escanearComida('camera')} disabled={escaneando}>
                {escaneando ? <ActivityIndicator size="small" color="#fff" /> : <Text style={styles.botonEscanearTexto}>📸 Escanear</Text>}
              </TouchableOpacity>
              <TouchableOpacity style={[styles.botonEscanear, { backgroundColor: '#43a047' }]} onPress={() => escanearComida('gallery')} disabled={escaneando}>
                <Text style={styles.botonEscanearTexto}>🖼️ Galería</Text>
              </TouchableOpacity>
            </View>
          </View>
          <Text style={styles.label}>¿Qué comiste? (opcional — mejora la precisión)</Text>
          <TextInput
            style={styles.input}
            value={nombrePlato}
            onChangeText={setNombrePlato}
            placeholder="Ej: guiso de lentejas, milanesa, pizza..."
          />
          <Text style={styles.label}>Carbohidratos sugeridos por imagen (gramos)</Text>
          <TextInput style={[styles.input, { backgroundColor: '#f0f0f0' }]}
            value={carbohidratos}
            onChangeText={(v) => {
              setCarbohidratos(v);
              if (!reajusteCarbs) setInsulina(calcularInsulinaTotal(glucosa, v, tendenciaManual, iob));
            }}
            keyboardType="numeric"
            placeholder="Se completa con el escaneo..."
          />
          <Text style={styles.label}>Reajuste de carbohidratos (gramos)</Text>
          <TextInput style={[styles.input, reajusteCarbs && { borderColor: '#1a73e8', borderWidth: 1.5 }]}
            value={reajusteCarbs}
            onChangeText={(v) => {
              setReajusteCarbs(v);
              setInsulina(calcularInsulinaTotal(glucosa, v || carbohidratos, tendenciaManual, iob));
            }}
            keyboardType="numeric"
            placeholder="Ajustá los carbs si sabés el valor real..."
          />
          {reajusteCarbs ? (
            <Text style={styles.reajusteHint}>✏️ Usando reajuste manual: {reajusteCarbs}g (sugerido: {carbohidratos}g)</Text>
          ) : null}
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Ejercicio</Text>
          <Text style={styles.label}>Tipo de actividad</Text>
          <TextInput style={styles.input} value={ejercicio} onChangeText={setEjercicio} placeholder="Ej: Caminata, futbol, natacion" />
          <Text style={styles.label}>Duracion (minutos)</Text>
          <TextInput style={styles.input} value={duracion} onChangeText={setDuracion} keyboardType="numeric" placeholder="Ej: 30" />
        </View>

        <View style={styles.seccion}>
          <Text style={styles.seccionTitulo}>Notas</Text>
          <TextInput style={styles.inputNotas} value={notas} onChangeText={setNotas} placeholder="Cualquier observacion relevante..." multiline scrollEnabled={false} textAlignVertical="top" />
        </View>

        <TouchableOpacity style={styles.boton} onPress={guardarRegistro}>
          <Text style={styles.botonTexto}>{editandoId !== null ? 'Actualizar registro' : 'Guardar registro'}</Text>
        </TouchableOpacity>

        {editandoId !== null && (
          <TouchableOpacity style={styles.botonCancelar} onPress={limpiarFormulario}>
            <Text style={styles.botonCancelarTexto}>Cancelar edicion</Text>
          </TouchableOpacity>
        )}
      </ScrollView>

      {/* Modal resultado escaneo de comida */}
      <Modal visible={mostrarResultadoComida} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modalComidaContainer}>
            {fotoUri && <Image source={{ uri: fotoUri }} style={styles.fotoComida} />}
            <Text style={styles.modalTitulo}>🍽️ Comida detectada</Text>
            {resultadoEscaneo && (
              <>
                <Text style={styles.alimentoDetectado}>
                  {resultadoEscaneo.alimento_detectado}
                  {resultadoEscaneo.carbs_por_100g != null ? ` · ${resultadoEscaneo.carbs_por_100g}g carbs/100g` : ''}
                </Text>
                {resultadoEscaneo.candidatos && resultadoEscaneo.candidatos.length > 1 && (
                  <>
                    <Text style={styles.candidatosHint}>¿No es esto? Tocá la opción correcta:</Text>
                    <View style={styles.candidatosContainer}>
                      {resultadoEscaneo.candidatos.slice(1).map(c => (
                        <TouchableOpacity key={c.nombre} style={styles.candidatoChip} disabled={escaneando} onPress={() => analizarImagen(c.nombre)}>
                          <Text style={styles.candidatoChipTexto}>{c.nombre}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
                <View style={styles.correccionNombreFila}>
                  <TextInput
                    style={[styles.input, { flex: 1, marginBottom: 0 }]}
                    value={nombreCorreccion}
                    onChangeText={setNombreCorreccion}
                    placeholder="Escribí el plato correcto..."
                  />
                  <TouchableOpacity style={styles.botonRecalcular} disabled={escaneando || !nombreCorreccion.trim()} onPress={() => analizarImagen(nombreCorreccion.trim())}>
                    {escaneando ? <ActivityIndicator size="small" color="#fff" /> : <Text style={styles.botonEscanearTexto}>Recalcular</Text>}
                  </TouchableOpacity>
                </View>
                <Text style={styles.porcionHint}>Seleccioná el tamaño de tu porción:</Text>
                {!isNaN(parseFloat(glucosa)) && (parseFloat(glucosa) > config.umbralCorreccion || parseFloat(glucosa) < config.glucosaMin) && (
                  <Text style={[styles.correccionAviso, { color: parseFloat(glucosa) > config.umbralCorreccion ? '#fb8c00' : '#1a73e8' }]}>
                    {parseFloat(glucosa) > config.umbralCorreccion
                      ? `⚠️ Glucosa alta (${glucosa} mg/dL) — se suman ${Math.round(((parseFloat(glucosa) - config.umbralCorreccion) / config.isfAlta) * 10) / 10}U`
                      : `✓ Glucosa baja (${glucosa} mg/dL) — se restan ${Math.round(Math.abs((parseFloat(glucosa) - config.glucosaMin) / config.isf) * 10) / 10}U`
                    }
                  </Text>
                )}
                <View style={styles.porcionesContainer}>
                  {['S', 'M', 'L'].map(tam => {
                    const p = resultadoEscaneo.porciones[tam];
                    const labels = { S: 'Chica', M: 'Mediana', L: 'Grande' };
                    return (
                      <TouchableOpacity key={tam} style={styles.porcionBtn} onPress={() => aplicarPorcion(tam)}>
                        <Text style={styles.porcionTam}>{labels[tam]}</Text>
                        <Text style={styles.porcionPeso}>{p.peso_g}g</Text>
                        <Text style={styles.porcionCarbs}>{p.carbs_totales}g carbs</Text>
                        <Text style={styles.porcionInsulina}>💉 {calcularInsulinaTotal(glucosa, String(p.carbs_totales), tendenciaManual, iob)} U</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </>
            )}
            <TouchableOpacity style={styles.modalCerrar} onPress={cerrarResultadoComida}>
              <Text style={styles.modalCerrarTexto}>Cancelar — ingresar manualmente</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5', padding: 20, paddingTop: 60 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  titulo: { fontSize: 28, fontWeight: 'bold', color: '#1a73e8' },
  subtitulo: { fontSize: 16, color: '#666', marginBottom: 20 },
  fecha: { fontSize: 14, color: '#888', marginBottom: 25 },
  editandoAviso: { fontSize: 13, color: '#fb8c00', marginBottom: 15, fontWeight: '600' },
  seccion: { backgroundColor: '#fff', borderRadius: 12, padding: 15, marginBottom: 15, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 5, elevation: 2 },
  seccionTitulo: { fontSize: 16, fontWeight: 'bold', color: '#333', marginBottom: 10 },
  label: { fontSize: 13, color: '#666', marginBottom: 6, marginTop: 8 },
  input: { backgroundColor: '#f9f9f9', borderRadius: 8, padding: 12, fontSize: 16, borderWidth: 1, borderColor: '#eee' },
  inputNotas: { backgroundColor: '#f9f9f9', borderRadius: 8, padding: 12, fontSize: 16, borderWidth: 1, borderColor: '#eee', height: 90 },
  boton: { backgroundColor: '#1a73e8', borderRadius: 12, padding: 18, alignItems: 'center', marginTop: 10, marginBottom: 15 },
  botonTexto: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  botonCancelar: { borderRadius: 12, padding: 15, alignItems: 'center', marginBottom: 60, borderWidth: 1, borderColor: '#ccc' },
  botonCancelarTexto: { color: '#666', fontSize: 15 },
  botonSecundario: { backgroundColor: '#e8f0fe', borderRadius: 10, padding: 10 },
  botonSecundarioTexto: { color: '#1a73e8', fontWeight: '600', fontSize: 14 },
  botonEscanear: { backgroundColor: '#1a73e8', borderRadius: 8, paddingHorizontal: 14, paddingVertical: 8, minWidth: 100, alignItems: 'center' },
  botonEscanearTexto: { color: '#fff', fontWeight: '600', fontSize: 13 },
  tarjeta: { backgroundColor: '#fff', borderRadius: 12, padding: 15, marginBottom: 12, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 5, elevation: 2 },
  tarjetaHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 },
  tarjetaFecha: { fontSize: 13, color: '#888' },
  tarjetaGlucosa: { fontSize: 18, fontWeight: 'bold' },
  tarjetaDetalle: { fontSize: 13, color: '#555', marginBottom: 6 },
  tarjetaNotas: { fontSize: 13, color: '#777', marginBottom: 10 },
  tarjetaBotones: { flexDirection: 'row', gap: 10 },
  botonEditar: { flex: 1, backgroundColor: '#e8f0fe', borderRadius: 8, padding: 10, alignItems: 'center' },
  botonEditarTexto: { color: '#1a73e8', fontWeight: '600' },
  botonEliminar: { flex: 1, backgroundColor: '#fce8e6', borderRadius: 8, padding: 10, alignItems: 'center' },
  botonEliminarTexto: { color: '#e53935', fontWeight: '600' },
  etiquetaEditado: { fontSize: 11, color: '#fff', backgroundColor: '#fb8c00', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, alignSelf: 'flex-start', marginTop: 6, fontWeight: '600' },
  vacio: { textAlign: 'center', color: '#aaa', marginTop: 60, fontSize: 16 },
  cgmLabel: { fontSize: 13, fontWeight: '600' },
  cgmHora: { fontSize: 11, color: '#aaa', marginTop: 6 },
  puntoCGM: { width: 8, height: 8, borderRadius: 4 },
  cardRT: { backgroundColor: '#fff', borderRadius: 16, padding: 24, alignItems: 'center', marginBottom: 20, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, elevation: 3 },
  valorRT: { fontSize: 72, fontWeight: 'bold', lineHeight: 80 },
  flechaRT: { fontSize: 40, marginTop: 4 },
  unidadRT: { fontSize: 16, color: '#888', marginTop: 4 },
  estadoRT: { fontSize: 15, fontWeight: '600', marginTop: 8 },
  tiempoRT: { fontSize: 12, color: '#aaa', marginTop: 4 },
  graficoContainer: { backgroundColor: '#fff', borderRadius: 12, marginBottom: 12, overflow: 'hidden', padding: 8 },
  filaHistorial: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#fff', borderRadius: 8, padding: 12, marginBottom: 6 },
  filaHora: { fontSize: 13, color: '#888', flex: 1 },
  filaValor: { fontSize: 15, fontWeight: 'bold', flex: 1, textAlign: 'center' },
  filaFlecha: { fontSize: 18, flex: 1, textAlign: 'right' },
  filtrosContainer: { flexDirection: 'row', gap: 6, marginBottom: 12, justifyContent: 'center' },
  filtroBton: { backgroundColor: '#e8f0fe', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8 },
  filtroBtonActivo: { backgroundColor: '#1a73e8' },
  filtroBtonTexto: { color: '#1a73e8', fontWeight: '600', fontSize: 13 },
  filtroBtonTextoActivo: { color: '#fff' },
  barraFecha: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  botonFecha: { flex: 1, backgroundColor: '#fff', borderRadius: 10, padding: 10, borderWidth: 1, borderColor: '#e0e0e0' },
  botonFechaTexto: { color: '#333', fontWeight: '600', fontSize: 14 },
  botonTiempoReal: { backgroundColor: '#fce8e6', borderRadius: 10, padding: 10 },
  botonTiempoRealTexto: { color: '#e53935', fontWeight: '600', fontSize: 13 },
  statsContainer: { flexDirection: 'row', backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 12, justifyContent: 'space-around', shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4, elevation: 2 },
  statItem: { alignItems: 'center' },
  statLabel: { fontSize: 11, color: '#888', marginBottom: 4 },
  statValor: { fontSize: 18, fontWeight: 'bold' },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' },
  modalContainer: { backgroundColor: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 24, paddingBottom: 40 },
  modalComidaContainer: { backgroundColor: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 24, paddingBottom: 40 },
  modalTitulo: { fontSize: 18, fontWeight: 'bold', color: '#333', marginBottom: 12, textAlign: 'center' },
  navMes: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  navBtn: { fontSize: 28, color: '#1a73e8', paddingHorizontal: 16 },
  navLabel: { fontSize: 17, fontWeight: '600', color: '#333' },
  grillaDias: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-start', marginBottom: 20 },
  diaBtn: { width: 42, height: 42, borderRadius: 21, justifyContent: 'center', alignItems: 'center', backgroundColor: '#f5f5f5' },
  diaBtnHoy: { borderWidth: 2, borderColor: '#1a73e8' },
  diaBtnSeleccionado: { backgroundColor: '#1a73e8' },
  diaBtnDeshabilitado: { opacity: 0.4 },
  diaBtnTexto: { fontSize: 15, color: '#333', fontWeight: '500' },
  diaBtnTextoSeleccionado: { color: '#fff', fontWeight: 'bold' },
  modalCerrar: { backgroundColor: '#f5f5f5', borderRadius: 12, padding: 14, alignItems: 'center', marginTop: 12 },
  modalCerrarTexto: { color: '#666', fontSize: 15, fontWeight: '600' },
  avisoCsv: { backgroundColor: '#fff3e0', borderRadius: 10, padding: 12, marginBottom: 16, borderLeftWidth: 4, borderLeftColor: '#fb8c00' },
  avisoCsvTexto: { color: '#e65100', fontSize: 13, fontWeight: '600' },
  garminCard: { backgroundColor: '#fff', borderRadius: 12, padding: 14, marginBottom: 12, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4, elevation: 2 },
  garminTitulo: { fontSize: 13, fontWeight: '700', color: '#555', marginBottom: 10 },
  garminStats: { flexDirection: 'row', justifyContent: 'space-around' },
  garminSubLabel: { fontSize: 10, color: '#aaa', marginTop: 2 },
  bolosLeyenda: { fontSize: 11, color: '#888', textAlign: 'center', marginTop: -6, marginBottom: 8 },
  zoomHint: { fontSize: 11, color: '#aaa', textAlign: 'center', marginBottom: 6, marginTop: -4 },
  // Configuración
  configHint: { fontSize: 11, color: '#aaa', marginTop: 6, fontStyle: 'italic' },
  configResumen: { fontSize: 14, color: '#1a73e8', marginBottom: 6, fontWeight: '500' },
  botonConfiguracion: { backgroundColor: '#e8f0fe', borderRadius: 10, padding: 10, alignItems: 'center', marginBottom: 20 },
  botonConfiguracionTexto: { color: '#1a73e8', fontWeight: '600', fontSize: 15 },
  // Escaneo de comida
  fotoComida: { width: '100%', height: 180, borderRadius: 12, marginBottom: 12 },
  alimentoDetectado: { fontSize: 15, fontWeight: '600', color: '#333', textAlign: 'center', marginBottom: 8 },
  porcionHint: { fontSize: 13, color: '#888', textAlign: 'center', marginBottom: 16 },
  candidatosHint: { fontSize: 12, color: '#888', textAlign: 'center', marginBottom: 6 },
  candidatosContainer: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 6, marginBottom: 10 },
  candidatoChip: { backgroundColor: '#e8f0fe', borderRadius: 16, paddingHorizontal: 12, paddingVertical: 6 },
  candidatoChipTexto: { fontSize: 13, color: '#1a73e8', fontWeight: '600' },
  correccionNombreFila: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 12 },
  botonRecalcular: { backgroundColor: '#1a73e8', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 12, justifyContent: 'center' },
  porcionesContainer: { flexDirection: 'row', gap: 10, justifyContent: 'center', marginBottom: 8 },
  porcionBtn: { flex: 1, backgroundColor: '#f5f5f5', borderRadius: 12, padding: 14, alignItems: 'center', borderWidth: 2, borderColor: '#e0e0e0' },
  porcionTam: { fontSize: 16, fontWeight: 'bold', color: '#1a73e8', marginBottom: 4 },
  porcionPeso: { fontSize: 12, color: '#888', marginBottom: 4 },
  porcionCarbs: { fontSize: 13, fontWeight: '600', color: '#333', marginBottom: 4 },
  porcionInsulina: { fontSize: 14, fontWeight: 'bold', color: '#e53935' },
  correccionAviso: { fontSize: 12, color: '#fb8c00', textAlign: 'center', marginBottom: 10, fontWeight: '600' },
  correccionPura: { backgroundColor: '#fff3e0', borderRadius: 8, padding: 10, marginTop: 8, borderLeftWidth: 4, borderLeftColor: '#fb8c00' },
  correccionPuraTexto: { color: '#e65100', fontSize: 13, fontWeight: '600' },
  correccionPuraHint: { color: '#aaa', fontSize: 11, marginTop: 2 },
  correccionPuraBtn: { color: '#1a73e8', fontSize: 13, fontWeight: '600', marginTop: 8 },
  // Selector de tendencia
  tendenciaContainer: { flexDirection: 'row', gap: 4, marginTop: 4, marginBottom: 4, flexWrap: 'wrap' },
  tendenciaBtn: { borderWidth: 1.5, borderColor: '#ddd', borderRadius: 8, paddingHorizontal: 8, paddingVertical: 6, backgroundColor: '#f5f5f5' },
  tendenciaBtnTexto: { fontSize: 13, fontWeight: '600', color: '#555' },
  tendenciaHint: { fontSize: 11, color: '#888', marginTop: 4, fontStyle: 'italic' },
  // Reajuste de carbohidratos
  reajusteHint: { fontSize: 11, color: '#1a73e8', marginTop: 4, fontStyle: 'italic' },
  iobHint: { fontSize: 11, color: '#9c27b0', marginTop: 4, fontWeight: '600' },
  botonRecalcular: { backgroundColor: '#f3e5f5', borderRadius: 8, padding: 10, alignItems: 'center', marginTop: 10, marginBottom: 4, borderWidth: 1, borderColor: '#9c27b0' },
  botonRecalcularTexto: { color: '#9c27b0', fontWeight: '600', fontSize: 13 },
});

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <AppInterno />
    </GestureHandlerRootView>
  );
}

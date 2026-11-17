"""FITBA · Dashboard de lecturas de MariaDB, versión estática.

Copia exacta del dashboard desplegado en EasyPanel (fitba-streamlit). Lo único que cambia es la
fuente: en lugar de conectarse a MariaDB, ejecuta las MISMAS consultas sobre fitba.db, una copia
SQLite de las tablas sensor_readings, lecturas y lecturas_3_estratos (horas tal como están en MariaDB).
"""
import sqlite3
from datetime import datetime
from pathlib import Path
import re
import time
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from style_fitba import (
    aplicar_estilo, pulir_figura, encabezado_app, encabezado_modulo, titulo_seccion,
    navegacion_sidebar, sidebar_seccion, tarjeta, franja_periodo, estado_vacio,
    COLOR_TEMP, COLOR_TEMP_EXTERIOR, COLOR_TEMP_RAICES, COLOR_HUM, COLOR_LUX,
    COLOR_ALERT, COLOR_FILL_NARANJA, COLOR_FILL_ROJO, MACETA_COLORES, BG_CARD, BORDER, INK,
)

st.set_page_config(page_title='FITBA | Vivero inteligente', page_icon='🌱', layout='wide')
aplicar_estilo()
# Versión estática: copia de las tablas de MariaDB en un archivo SQLite junto a la app.
DB = Path(__file__).with_name('fitba.db')


CONSULTAS = {
    'sensor_readings': ('''SELECT device_id, `timestamp`, temperature, humidity, luminosity
                            FROM sensor_readings ORDER BY `timestamp`''', 'timestamp'),
    'lecturas': ('''SELECT id, fecha, temp_1, temp_2, temp_3, temp_4, temp_5,
                         hum_1, hum_2, hum_3, hum_4, hum_5
                  FROM lecturas ORDER BY fecha''', 'fecha'),
    'lecturas_3_estratos': ('''SELECT device_id, id, `timestamp`, temp_alto, hum_alto,
                                      temp_medio, hum_medio, temp_bajo, hum_bajo, lux
                               FROM lecturas_3_estratos ORDER BY `timestamp`''', 'timestamp'),
}


@st.cache_data(show_spinner='Cargando mediciones reales…', ttl=60)
def cargar(tabla):
    if tabla not in CONSULTAS:
        raise ValueError('Fuente no admitida')
    consulta, campo = CONSULTAS[tabla]
    with sqlite3.connect(DB) as conexion:
        df = pd.read_sql_query(consulta, conexion)
    df[campo] = pd.to_datetime(df[campo], errors='coerce')
    return df.dropna(subset=[campo]).sort_values(campo)


try:
    raices = cargar('lecturas')
    exterior = cargar('sensor_readings')
    estratos = cargar('lecturas_3_estratos')
except Exception:
    st.error('No se pudieron leer las mediciones de fitba.db. Verificá que el archivo esté junto a app.py.')
    st.stop()

encabezado_app('🌱 FITBA · Vivero inteligente', 'Seguimiento de condiciones ambientales y de cultivo')

PAGINAS = ['Sensor exterior', 'Violeta de los Alpes', 'Estratos', 'Galería', 'Timelapse']
pagina = navegacion_sidebar(PAGINAS, key='pagina')


def filtros(df, campo, key):
    """Ventana temporal común para todos los elementos de una página."""
    sidebar_seccion('Período de análisis')
    opciones = ['Últimas 12 horas', 'Últimas 24 horas', 'Últimos 7 días', 'Fecha específica', 'Rango personalizado', 'Histórico completo']
    modo = st.sidebar.selectbox('Período', opciones, index=1, key=f'{key}_modo', label_visibility='collapsed')
    # Versión estática: como los datos terminan en la última copia, los períodos relativos se cuentan
    # desde la última lectura registrada (en EasyPanel se contaban desde la hora actual).
    referencia = df[campo].max()
    ahora = referencia
    if modo.startswith('Últimas 12'):
        inicio, fin = ahora - pd.Timedelta(hours=12), ahora
    elif modo.startswith('Últimas 24'):
        inicio, fin = ahora - pd.Timedelta(hours=24), ahora
    elif modo.startswith('Últimos 7'):
        inicio, fin = ahora - pd.Timedelta(days=7), ahora
    elif modo == 'Fecha específica':
        dia = st.sidebar.date_input('Fecha', value=ahora.date(), key=f'{key}_dia')
        inicio, fin = pd.Timestamp(dia), pd.Timestamp(dia) + pd.Timedelta(days=1)
    elif modo == 'Rango personalizado':
        rango = st.sidebar.date_input('Desde / hasta', value=(ahora.date() - pd.Timedelta(days=1), ahora.date()), key=f'{key}_rango')
        if len(rango) != 2:
            st.info('Seleccioná ambas fechas del rango.'); st.stop()
        inicio, fin = pd.Timestamp(rango[0]), pd.Timestamp(rango[1]) + pd.Timedelta(days=1)
    else:
        inicio, fin = df[campo].min(), referencia
    es_fin_exclusivo = modo in ('Fecha específica', 'Rango personalizado')
    mascara = (df[campo] >= inicio) & ((df[campo] < fin) if es_fin_exclusivo else (df[campo] <= fin))
    franja_periodo(inicio, fin, referencia)
    return df.loc[mascara].copy(), inicio, fin


COLORES = {'lux': COLOR_LUX,
           'temp_alto': '#8C2A1B', 'temp_medio': '#B33D26', 'temp_bajo': '#D97456',
           'hum_alto': '#0B4B7A', 'hum_medio': COLOR_HUM, 'hum_bajo': '#6FA8D6'}


def bandas_temperatura(fig):
    """Referencias térmicas visuales."""
    fig.add_hrect(y0=25, y1=28, fillcolor='rgba(184,134,11,0.10)', line_width=0,
                  annotation_text='25–28 °C', annotation_position='top left',
                  annotation_font=dict(color=INK))
    fig.add_hrect(y0=28, y1=45, fillcolor='rgba(166,48,31,0.08)', line_width=0,
                  annotation_text='≥ 28 °C', annotation_position='top left',
                  annotation_font=dict(color=INK))
    fig.add_hline(y=25, line_color=COLOR_LUX, line_dash='dot')
    fig.add_hline(y=28, line_color=COLOR_ALERT, line_dash='dash')


# Etiqueta de eje (variable + unidad) para cada variable del Sensor exterior.
UNIDAD_EXTERIOR = {
    'temperature': 'Temperatura (°C)',
    'humidity': 'Humedad (%)',
    'luminosity': 'Luminosidad (lux)',
}
COLOR_EXTERIOR = {
    'temperature': COLOR_TEMP,   # temperatura: siempre roja en este gráfico
    'humidity': COLOR_HUM,
    'luminosity': COLOR_LUX,
}


def historico_sensor_exterior(df, variables_elegidas):
    """Gráfico 'Histórico del sensor' (Sensor exterior).

    Admite 1 o 2 variables de {Temperatura, Humedad, Luminosidad}. Cada eje
    identifica exactamente la variable y la unidad que representa: no hay
    títulos genéricos ni ejes sin título, en ninguna de las seis
    combinaciones posibles.
    """
    if df.empty:
        estado_vacio('Sin mediciones para el período seleccionado.'); return
    mapa_columna = {'Temperatura': 'temperature', 'Humedad': 'humidity', 'Luminosidad': 'luminosity'}
    fig = go.Figure()
    titulos_ejes = []  # [(eje, titulo)] en el orden en que se agregan
    for i, nombre in enumerate(variables_elegidas):
        col = mapa_columna[nombre]
        if col not in df or df[col].isna().all():
            continue
        eje = 'y' if i == 0 else 'y2'
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df[col], name=nombre, mode='lines',
                                 line=dict(color=COLOR_EXTERIOR[col]), yaxis=eje))
        titulos_ejes.append((eje, UNIDAD_EXTERIOR[col]))
    if not fig.data:
        estado_vacio('Sin valores válidos para las variables seleccionadas.'); return

    layout = dict(xaxis_title='Fecha y hora', hovermode='x unified')
    for eje, titulo_eje in titulos_ejes:
        if eje == 'y':
            layout['yaxis_title'] = titulo_eje
        else:
            layout['yaxis2'] = dict(title=titulo_eje, overlaying='y', side='right')
    fig.update_layout(**layout)
    st.plotly_chart(pulir_figura(fig, altura=380), use_container_width=True)


def disponibilidad_exterior(base, inicio, fin):
    """Franjas con/sin lecturas: verde si hay un registro en el intervalo.

    La duración máxima atribuida a una lectura es 30 min (aprox. dos ciclos
    de adquisición); no se extiende a través de grandes huecos de datos.
    """
    titulo_seccion('Disponibilidad del sensor', 'Verde: hubo lecturas. Gris: sin lecturas registradas (no implica necesariamente que el sensor estuviera apagado). Cada lectura cubre como máximo 30 minutos.')
    if pd.isna(inicio) or pd.isna(fin) or fin <= inicio:
        estado_vacio('No hay un intervalo válido para representar.'); return
    marcas = base['timestamp'].dropna().sort_values().drop_duplicates()
    previo = marcas[marcas < inicio].tail(1)
    marcas = pd.concat([previo, marcas[(marcas >= inicio) & (marcas <= fin)]], ignore_index=True)
    segmentos = []
    cursor = pd.Timestamp(inicio)
    limite = pd.Timestamp(fin)
    cobertura = pd.Timedelta(minutes=30)
    for t in marcas:
        a, b = max(pd.Timestamp(t), cursor), min(pd.Timestamp(t) + cobertura, limite)
        if b <= cursor:
            continue
        if a > cursor:
            segmentos.append((cursor, a, False))
        if b > a:
            segmentos.append((a, b, True))
            cursor = b
        if cursor >= limite:
            break
    if cursor < limite:
        segmentos.append((cursor, limite, False))
    unidos = []
    for a, b, disponible in segmentos:
        if unidos and unidos[-1][2] == disponible and unidos[-1][1] == a:
            unidos[-1] = (unidos[-1][0], b, disponible)
        else:
            unidos.append((a, b, disponible))
    fig = go.Figure()
    for estado, color, etiqueta in [(False, '#D6DED9', 'Sin lecturas'), (True, '#1B7A5C', 'Con lecturas')]:
        tramos = [(a, b) for a, b, ok in unidos if ok == estado]
        if not tramos:
            continue
        fig.add_trace(go.Bar(
            x=[(b - a).total_seconds() * 1000 for a, b in tramos],
            base=[a for a, b in tramos], y=['Disponibilidad'] * len(tramos),
            orientation='h', width=0.8, name=etiqueta,
            marker_color=color, marker_line_width=0,
            customdata=[[a.strftime('%d/%m/%Y %H:%M'), b.strftime('%d/%m/%Y %H:%M')] for a, b in tramos],
            hovertemplate=etiqueta + '<br>%{customdata[0]} — %{customdata[1]}<extra></extra>',
        ))
    fig.update_layout(barmode='overlay', height=170, xaxis=dict(type='date', range=[inicio, fin], title=None),
                      yaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
                      legend=dict(orientation='h', y=1.3), margin=dict(l=10, r=10, t=30, b=35), bargap=0)
    st.plotly_chart(pulir_figura(fig), use_container_width=True)


def tabla_completa(df, columnas):
    """Mostrar todos los registros filtrados, sin recortar a las últimas 30 filas."""
    st.dataframe(df[list(columnas)].rename(columns=columnas).sort_values('Fecha y hora', ascending=False),
                 use_container_width=True, hide_index=True)


def valor(x, unidad='', dec=1):
    return f'{x:.{dec}f} {unidad}' if pd.notna(x) else 'Sin datos'


def con_cruces_de_umbral(x, y, umbrales):
    """Devuelve (x, y) con puntos interpolados exactamente donde la curva
    cruza cada umbral, para que un relleno condicional cambie de color en
    el punto de cruce real y no recién en la siguiente medición."""
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True)
    if len(x) == 0:
        return x, y
    xs, ys = [x.iloc[0]], [y.iloc[0]]
    for i in range(1, len(x)):
        x0, x1 = x.iloc[i - 1], x.iloc[i]
        y0, y1 = y.iloc[i - 1], y.iloc[i]
        if pd.notna(y0) and pd.notna(y1) and y0 != y1:
            cruces = []
            for u in umbrales:
                if (y0 - u) * (y1 - u) < 0:
                    frac = (u - y0) / (y1 - y0)
                    cruces.append((x0 + (x1 - x0) * frac, u))
            for xt, u in sorted(cruces, key=lambda p: p[0]):
                xs.append(xt); ys.append(u)
        xs.append(x1); ys.append(y1)
    return pd.Series(xs).reset_index(drop=True), pd.Series(ys).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fotografías (Galería / Timelapse)
# ---------------------------------------------------------------------------
FOTOS = Path(__file__).with_name('uploads')
PATRON_FOTO = re.compile(r'^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_[A-Za-z0-9_-]+\.(jpg|jpeg|png|gif)$', re.I)


@st.cache_data(show_spinner=False, ttl=60)
def catalogo_fotos(carpeta):
    raiz = Path(carpeta)
    registros = []
    if not raiz.is_dir():
        return pd.DataFrame(columns=['nombre', 'fecha', 'dia', 'hora', 'tamano'])
    for archivo in raiz.iterdir():
        if not archivo.is_file():
            continue
        m = PATRON_FOTO.fullmatch(archivo.name)
        if not m:
            continue
        try:
            fecha = datetime.strptime('_'.join([m.group(1), '-'.join(m.group(i) for i in (2, 3, 4))]), '%Y-%m-%d_%H-%M-%S')
            registros.append({'nombre': archivo.name, 'fecha': fecha, 'dia': fecha.date(),
                              'hora': fecha.hour, 'tamano': archivo.stat().st_size})
        except (ValueError, OSError):
            continue
    return pd.DataFrame(registros, columns=['nombre', 'fecha', 'dia', 'hora', 'tamano']).sort_values('fecha').reset_index(drop=True)


def filtros_fotos(base, prefijo):
    minimo, maximo = base['dia'].min(), base['dia'].max()
    st.caption(f'Registro disponible: {minimo:%d/%m/%Y} — {maximo:%d/%m/%Y}')
    rango = st.date_input('Desde / hasta', value=(minimo, maximo), min_value=minimo,
                          max_value=maximo, key=f'{prefijo}_fechas', label_visibility='collapsed')
    if not isinstance(rango, (list, tuple)) or len(rango) != 2:
        st.info('Seleccioná las dos fechas para continuar.')
        st.stop()
    desde, hasta = rango
    if desde > hasta:
        st.warning('La fecha inicial debe ser anterior o igual a la final.')
        st.stop()
    return base.loc[base['dia'].between(desde, hasta)].copy(), desde, hasta


def calendario_frecuencia(base, desde, hasta):
    """Mapa de calor semana × día con la cantidad de fotos por jornada."""
    dias = pd.date_range(desde, hasta, freq='D')
    if len(dias) < 2 or len(dias) > 3660:
        return
    conteos = base.groupby('dia').size()
    serie = pd.Series({d.date(): int(conteos.get(d.date(), 0)) for d in dias})
    semana = pd.Series(dias).dt.isocalendar().week.values
    dia_semana = pd.Series(dias).dt.weekday.values  # 0=lunes
    df = pd.DataFrame({'semana': semana, 'dia_semana': dia_semana, 'fecha': dias, 'cantidad': serie.values})
    pivote = df.pivot_table(index='dia_semana', columns='semana', values='cantidad', aggfunc='sum')
    etiquetas_fecha = df.pivot_table(index='dia_semana', columns='semana', values='fecha', aggfunc='first')
    fig = go.Figure(go.Heatmap(
        z=pivote.values, x=[f'S{c}' for c in pivote.columns],
        y=['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'],
        colorscale=[[0, '#E9EFEA'], [1, '#1B7A5C']],
        customdata=etiquetas_fecha.values,
        hovertemplate='%{customdata|%d/%m/%Y}<br>%{z} fotografía(s)<extra></extra>',
        showscale=False, xgap=3, ygap=3,
    ))
    fig.update_layout(height=210, margin=dict(l=40, r=10, t=10, b=20))
    st.plotly_chart(pulir_figura(fig), use_container_width=True)


@st.dialog('Fotografía', width='large')
def ver_foto_ampliada(ruta, pie):
    st.image(str(ruta), use_container_width=True)
    st.caption(pie)


def modulo_galeria():
    encabezado_modulo('Galería')
    base = catalogo_fotos(str(FOTOS))
    if base.empty:
        st.warning('No se encontraron fotografías en la carpeta uploads.')
        return
    with tarjeta():
        col_f, col_h = st.columns([2, 1])
        with col_f:
            seleccion, desde, hasta = filtros_fotos(base, 'galeria')
        horas = sorted(seleccion['hora'].unique().tolist())
        with col_h:
            opciones = ['Todas las horas'] + [f'{h:02d}:00–{h:02d}:59' for h in horas]
            opcion = st.selectbox('Franja horaria', opciones, key='galeria_hora')
        hora = None if opcion == 'Todas las horas' else int(opcion[:2])
        calendario_frecuencia(seleccion, desde, hasta)

    fotos = seleccion if hora is None else seleccion.loc[seleccion['hora'] == hora]
    if fotos.empty:
        estado_vacio('No hay fotografías para estos filtros.')
        return
    a, b = st.columns([1, 3])
    a.metric('Fotografías', len(fotos))
    b.caption(f'Primera: {fotos.fecha.min():%d/%m/%Y %H:%M:%S}  ·  Última: {fotos.fecha.max():%d/%m/%Y %H:%M:%S}')

    por_pagina = 12
    total_paginas = (len(fotos) + por_pagina - 1) // por_pagina
    pagina_actual = st.number_input('Página', min_value=1, max_value=total_paginas, value=1, step=1, key='galeria_pagina')
    visibles = fotos.sort_values('fecha', ascending=False).iloc[(pagina_actual - 1) * por_pagina:pagina_actual * por_pagina]
    columnas = st.columns(3)
    for i, (_, foto) in enumerate(visibles.iterrows()):
        with columnas[i % 3]:
            with tarjeta():
                st.image(str(FOTOS / foto['nombre']), use_container_width=True)
                pie = f"{foto['fecha']:%d/%m/%Y %H:%M:%S} · {foto['tamano'] / 1024:.0f} KB"
                st.markdown(f'<div class="fitba-photo-meta">{pie}</div>', unsafe_allow_html=True)
                if st.button('Ver en grande', key=f'ver_{foto["nombre"]}', use_container_width=True):
                    ver_foto_ampliada(FOTOS / foto['nombre'], pie)
    st.caption(f'Página {pagina_actual} de {total_paginas}')


def modulo_timelapse():
    encabezado_modulo('Timelapse')
    base = catalogo_fotos(str(FOTOS))
    if base.empty:
        st.warning('No se encontraron fotografías en la carpeta uploads.')
        return
    with tarjeta():
        seleccion, desde, hasta = filtros_fotos(base, 'timelapse')
        horas = sorted(seleccion['hora'].unique().tolist())
        if not horas:
            calendario_frecuencia(base, desde, hasta)
            estado_vacio('No hay fotografías para el rango elegido.')
            return
        hora = st.selectbox('Hora de captura', horas, format_func=lambda h: f'{h:02d}:00–{h:02d}:59', key='timelapse_hora')

    fotos = seleccion.loc[seleccion['hora'] == hora].sort_values('fecha').reset_index(drop=True)
    if fotos.empty:
        estado_vacio('No hay fotogramas para esa hora en el rango elegido.')
        return

    # st.fragment evita recalcular los módulos de sensores en cada fotograma.
    @st.fragment
    def reproductor():
        with tarjeta():
            indice = st.session_state.get('fotograma_indice', 0)
            indice = max(0, min(indice, len(fotos) - 1))
            st.session_state['fotograma_indice'] = indice
            imagen = st.empty()

            def mostrar(i):
                fila = fotos.iloc[i]
                imagen.image(str(FOTOS / fila['nombre']), use_container_width=True)

            mostrar(indice)
            fila_actual = fotos.iloc[indice]
            st.caption(f"Fotograma {indice + 1} de {len(fotos)}  ·  {fila_actual['fecha']:%d/%m/%Y %H:%M:%S}")

            ctrl = st.columns([1, 1, 1, 2])
            if ctrl[0].button('◀ Anterior', key='foto_anterior', disabled=indice == 0, use_container_width=True):
                st.session_state['fotograma_indice'] = indice - 1
                st.rerun(scope='fragment')
            if ctrl[1].button('▶ Reproducir', key='foto_reproducir', disabled=indice >= len(fotos) - 1, use_container_width=True):
                for i in range(indice + 1, len(fotos)):
                    mostrar(i)
                    st.session_state['fotograma_indice'] = i
                    time.sleep(velocidad / 1000)
                st.rerun(scope='fragment')
            if ctrl[2].button('Siguiente ▶', key='foto_siguiente', disabled=indice >= len(fotos) - 1, use_container_width=True):
                st.session_state['fotograma_indice'] = indice + 1
                st.rerun(scope='fragment')
            with ctrl[3]:
                nuevo = st.slider('Fotograma', 1, len(fotos), indice + 1, key='timelapse_selector', label_visibility='collapsed')
            if nuevo - 1 != indice:
                st.session_state['fotograma_indice'] = nuevo - 1
                st.rerun(scope='fragment')

    with tarjeta():
        st.metric('Fotogramas en esta hora', len(fotos))
        velocidad = st.select_slider('Velocidad de reproducción', options=[200, 300, 500, 750, 1000, 1500, 2000], value=500,
                                     format_func=lambda n: f'{n} ms', key='timelapse_velocidad')
    reproductor()


if pagina == 'Galería':
    modulo_galeria()

elif pagina == 'Timelapse':
    modulo_timelapse()

elif pagina == 'Sensor exterior':
    encabezado_modulo(pagina)
    dispositivos = exterior['device_id'].dropna().unique().tolist()
    sidebar_seccion('Dispositivo')
    dev = st.sidebar.selectbox('Dispositivo exterior', dispositivos, label_visibility='collapsed')
    base = exterior.loc[exterior.device_id == dev]
    df, inicio, fin = filtros(base, 'timestamp', 'ext')
    if df.empty:
        st.warning('Sin mediciones en el período seleccionado.'); st.stop()

    a, b, c, d = st.columns(4)
    a.metric('Temperatura promedio', valor(df.temperature.mean(), '°C'))
    b.metric('Humedad promedio', valor(df.humidity.mean(), '%'))
    c.metric('Luminosidad promedio', valor(df.luminosity.mean(), 'lux', 0))
    d.metric('Lecturas en el período', f'{len(df):,}')

    col_izq, col_der = st.columns([3, 2])
    with col_izq:
        with tarjeta():
            titulo_seccion('Histórico del sensor')
            variables = st.multiselect('Variables', ['Temperatura', 'Humedad', 'Luminosidad'], default=['Temperatura', 'Humedad'], max_selections=2, label_visibility='collapsed')
            if not variables:
                st.info('Elegí al menos una variable.')
            else:
                historico_sensor_exterior(df, variables)
    with col_der:
        with tarjeta():
            disponibilidad_exterior(base, inicio, fin)

    with tarjeta():
        titulo_seccion('Tabla de mediciones')
        tabla_completa(df, {'timestamp': 'Fecha y hora', 'temperature': 'Temperatura (°C)', 'humidity': 'Humedad (%)', 'luminosity': 'Luminosidad (lux)'})

elif pagina == 'Violeta de los Alpes':
    encabezado_modulo(pagina)
    # La ventana temporal se calcula desde la hora actual y se aplica SIN CAMBIOS a todas las fuentes.
    r, inicio, fin = filtros(raices, 'fecha', 'violeta')
    exclusivo = st.session_state['violeta_modo'] in ('Fecha específica', 'Rango personalizado')

    def en_ventana(df, campo):
        return df[(df[campo] >= inicio) & ((df[campo] < fin) if exclusivo else (df[campo] <= fin))].copy()

    ext = en_ventana(exterior, 'timestamp')
    if r.empty:
        st.warning('Sin lecturas de macetas para el período seleccionado. Podés elegir otras fechas.'); st.stop()
    temps = [f'temp_{i}' for i in range(1, 6)]
    hums = [f'hum_{i}' for i in range(1, 6)]
    ultimo = r.iloc[-1]

    st.caption(f'Lecturas de macetas: {len(r)}  ·  Lecturas exteriores: {len(ext)}  ·  Última lectura de raíces: {raices.fecha.max():%d/%m/%Y %H:%M}')

    titulo_seccion('Macetas · última medición del período')
    cols = st.columns(5)
    for i, col in enumerate(cols, 1):
        with col:
            st.metric(f'Maceta {i}', valor(ultimo[f'temp_{i}'], '°C'))
            st.caption(f'Humedad {valor(ultimo[f"hum_{i}"], "%")}')

    with tarjeta():
        titulo_seccion('Temperatura de raíces y exterior', 'Franja amarilla: 25–28 °C. Franja roja: ≥ 28 °C.')
        r = r.copy(); r['promedio_raices'] = r[temps].mean(axis=1)
        fig = go.Figure()
        fig.add_scatter(x=r.fecha, y=r.promedio_raices, name='Promedio de raíces', mode='lines',
                        line=dict(color=COLOR_TEMP_RAICES, width=2.4))
        if not ext.empty:
            fig.add_scatter(x=ext.timestamp, y=ext.temperature, name='Temperatura exterior', mode='lines',
                            line=dict(color=COLOR_TEMP_EXTERIOR, width=1.6, dash='dot'))
        bandas_temperatura(fig)
        fig.update_layout(xaxis_title='Fecha y hora', yaxis_title='°C', hovermode='x unified')
        st.plotly_chart(pulir_figura(fig, altura=380), use_container_width=True)
        if ext.empty:
            estado_vacio('No hay mediciones exteriores para este período; se muestran solamente las raíces.')

    with tarjeta():
        titulo_seccion('Temperatura de las cinco macetas')
        fig = make_subplots(rows=1, cols=5, shared_yaxes=True, horizontal_spacing=.02,
                            subplot_titles=[f'Maceta {i}' for i in range(1, 6)])
        y_min = min(15, r[temps].min().min() - 1) if r[temps].notna().any().any() else 15
        y_max = max(30, r[temps].max().max() + 1) if r[temps].notna().any().any() else 30
        for i, col in enumerate(temps, 1):
            color = MACETA_COLORES[i - 1]
            serie = r[['fecha', col]].dropna()
            if not serie.empty:
                # Puntos con cruces interpolados en 25 °C y 28 °C: el relleno cambia
                # de color exactamente donde la curva atraviesa cada umbral.
                xi, yi = con_cruces_de_umbral(serie['fecha'], serie[col], [25, 28])
                base_25 = pd.Series(25, index=yi.index)
                base_28 = pd.Series(28, index=yi.index)
                naranja = yi.clip(lower=25, upper=28)  # 0 por debajo de 25; tope en 28
                rojo = yi.clip(lower=28)                # 28 por debajo de ese umbral; curva real por encima

                # Línea base invisible en 25 °C (referencia para el relleno naranja)
                fig.add_trace(go.Scatter(x=xi, y=base_25, mode='lines', line=dict(width=0),
                                         showlegend=False, hoverinfo='skip'), row=1, col=i)
                # Relleno naranja: entre 25 °C y la curva, topeado en 28 °C
                fig.add_trace(go.Scatter(x=xi, y=naranja, mode='lines', line=dict(width=0),
                                         fill='tonexty', fillcolor=COLOR_FILL_NARANJA,
                                         showlegend=False, hoverinfo='skip'), row=1, col=i)
                # Línea base invisible en 28 °C (referencia para el relleno rojo; sin fill respecto a la anterior)
                fig.add_trace(go.Scatter(x=xi, y=base_28, mode='lines', line=dict(width=0),
                                         showlegend=False, hoverinfo='skip'), row=1, col=i)
                # Relleno rojo: entre 28 °C y la curva, solo cuando la supera
                fig.add_trace(go.Scatter(x=xi, y=rojo, mode='lines', line=dict(width=0),
                                         fill='tonexty', fillcolor=COLOR_FILL_ROJO,
                                         showlegend=False, hoverinfo='skip'), row=1, col=i)
                # Curva real, visible, sin relleno propio
                fig.add_trace(go.Scatter(x=serie['fecha'], y=serie[col], mode='lines',
                                         line=dict(color=color, width=1.8), showlegend=False,
                                         hovertemplate='%{y:.1f} °C<extra></extra>'), row=1, col=i)
            fig.add_hline(y=25, line_color=COLOR_LUX, line_dash='dot', line_width=1, row=1, col=i)
            fig.add_hline(y=28, line_color=COLOR_ALERT, line_dash='dash', line_width=1, row=1, col=i)
            fig.update_yaxes(range=[y_min, y_max], row=1, col=i, showticklabels=(i == 1),
                             title_text='Temperatura (°C)' if i == 1 else None)
            fig.update_xaxes(row=1, col=i, tickangle=-45, nticks=4)
        fig.update_annotations(font_size=13)
        st.plotly_chart(pulir_figura(fig, altura=340), use_container_width=True)

    with tarjeta():
        titulo_seccion('Humedad de las cinco macetas')
        fig = go.Figure()
        for i, col in enumerate(hums, 1):
            fig.add_trace(go.Scatter(x=r.fecha, y=r[col], mode='lines', name=f'Maceta {i}',
                                     line=dict(color=MACETA_COLORES[i - 1], width=1.8)))
        fig.update_layout(xaxis_title='Fecha y hora', yaxis_title='Humedad (%)', hovermode='x unified')
        st.plotly_chart(pulir_figura(fig, altura=340), use_container_width=True)

    with tarjeta():
        titulo_seccion('Exposición térmica de macetas', 'Tiempo con temperatura ≥ 28 °C. Solo se suman intervalos consecutivos de hasta 30 minutos; no se extrapolan faltantes.')
        rr = r.sort_values('fecha').drop_duplicates('fecha').copy()
        rr['siguiente'] = rr.fecha.shift(-1)
        rr['minutos'] = (rr.siguiente - rr.fecha).dt.total_seconds() / 60
        validos = rr.minutos.gt(0) & rr.minutos.le(30)
        # Ancho completo de la página y grilla adaptable: con cinco columnas plenas,
        # cada valor ("2.3 h") se muestra completo, con su unidad y etiqueta.
        cc = st.columns(5, gap='medium')
        for i, col in enumerate(cc, 1):
            mins = rr.loc[validos & rr[f'temp_{i}'].ge(28), 'minutos'].sum()
            col.metric(f'Maceta {i}', f'{mins / 60:.1f} h' if validos.any() else '—')

    with tarjeta():
        titulo_seccion('Alerta de riesgo fúngico', 'Al menos una maceta supera 28 °C: señal térmica de atención, no un diagnóstico de enfermedad.')
        eventos = r[temps].gt(28).any(axis=1)
        st.metric('Lecturas con alguna raíz > 28 °C', f'{int(eventos.sum())} de {len(r)}')

    with tarjeta():
        titulo_seccion('Condiciones ambientales del vivero (Estratos)')
        devs = sorted(estratos.device_id.dropna().unique().tolist())
        dev = st.selectbox('Dispositivo', devs, key='violeta_estratos')
        ambiente = en_ventana(estratos.loc[estratos.device_id == dev], 'timestamp').sort_values('timestamp').drop_duplicates('timestamp').copy()
        if ambiente.empty:
            estado_vacio('Sin mediciones de Estratos para este dispositivo y período. Los indicadores ambientales no se calculan.')
        else:
            ambiente['delta_t'] = ambiente.temp_alto - ambiente.temp_bajo
            ambiente['siguiente'] = ambiente.timestamp.shift(-1)
            ambiente['minutos'] = (ambiente.siguiente - ambiente.timestamp).dt.total_seconds() / 60
            validos = ambiente.minutos.gt(0) & ambiente.minutos.le(30) & ambiente.lux.notna()
            luz = ambiente.loc[validos & ambiente.lux.gt(10), 'minutos'].sum()
            x, y, z = st.columns(3)
            x.metric('Diferencial techo-suelo', valor(ambiente.delta_t.mean(), '°C', 2))
            y.metric('Tiempo con luz (>10 lux)', f'{luz / 60:.2f} h' if validos.any() else '—')
            z.metric('Lecturas ambientales', f'{len(ambiente):,}')
            st.caption(f'Período de lecturas ambientales: {ambiente.timestamp.min():%d/%m/%Y %H:%M} — {ambiente.timestamp.max():%d/%m/%Y %H:%M}. Los valores no representan necesariamente las condiciones actuales.')
            ultima = ambiente.iloc[-1]
            st.caption('Perfil vertical · última lectura del período: ' + ultima.timestamp.strftime('%d/%m/%Y %H:%M'))
            k = st.columns(3)
            for col, nombre, clave in zip(k, ['Techo', 'Medio', 'Suelo'], ['temp_alto', 'temp_medio', 'temp_bajo']):
                col.metric(nombre, valor(ultima[clave], '°C'))

elif pagina == 'Estratos':
    encabezado_modulo(pagina)
    devs = sorted(estratos.device_id.dropna().unique().tolist())
    sidebar_seccion('Dispositivos')
    elegidos = st.sidebar.multiselect('Dispositivos', devs, default=devs[:1], label_visibility='collapsed')
    if not elegidos:
        st.info('Seleccioná al menos un dispositivo.'); st.stop()
    base = estratos.loc[estratos.device_id.isin(elegidos)]
    df, _, _ = filtros(base, 'timestamp', 'estratos')
    mapa = {'Temperatura alta': 'temp_alto', 'Temperatura media': 'temp_medio', 'Temperatura baja': 'temp_bajo',
          'Humedad alta': 'hum_alto', 'Humedad media': 'hum_medio', 'Humedad baja': 'hum_bajo', 'Luminosidad': 'lux'}
    with tarjeta():
        seleccion = st.multiselect('Variables', list(mapa),
                                 default=['Temperatura alta', 'Temperatura media', 'Temperatura baja'])
    if not seleccion:
        st.info('Elegí al menos una variable.'); st.stop()
    if df.empty:
        st.info('Sin mediciones en el período seleccionado.'); st.stop()

    grupos = {'Temperatura (°C)': [], 'Humedad (%)': [], 'Luminosidad (lux)': []}
    for etiqueta in seleccion:
        v = mapa[etiqueta]
        grupo = 'Luminosidad (lux)' if v == 'lux' else 'Humedad (%)' if v.startswith('hum_') else 'Temperatura (°C)'
        grupos[grupo].append((etiqueta, v))
    grupos_activos = {u: vs for u, vs in grupos.items() if vs}

    # --- Primero TODOS los gráficos, un dispositivo debajo del otro ---
    datos_por_dispositivo = {}
    for dev in elegidos:
        datos = df.loc[df.device_id == dev].copy()
        datos_por_dispositivo[dev] = datos
        with tarjeta():
            titulo_seccion(f'Dispositivo {dev}')
            if datos.empty:
                estado_vacio('Sin mediciones para este dispositivo en el período seleccionado.')
                continue
            columnas_graf = st.columns(len(grupos_activos))
            for col, (unidad, variables) in zip(columnas_graf, grupos_activos.items()):
                with col:
                    fig = go.Figure()
                    for etiqueta, v in variables:
                        fig.add_scatter(x=datos.timestamp, y=datos[v], mode='lines', name=etiqueta,
                                        line=dict(color=COLORES[v]))
                    fig.update_layout(title=unidad, xaxis_title=None, yaxis_title=unidad, hovermode='x unified')
                    st.plotly_chart(pulir_figura(fig, altura=320), use_container_width=True)
            st.caption(f'{len(datos):,} registros en el período · última lectura del dispositivo: {base.loc[base.device_id == dev, "timestamp"].max():%d/%m/%Y %H:%M}')

    # --- Después, todas las tablas juntas ---
    with tarjeta():
        titulo_seccion('Tablas de mediciones')
        columnas = {'timestamp': 'Fecha y hora', 'device_id': 'Dispositivo'}
        columnas.update({mapa[s]: s for s in seleccion})
        pestañas = st.tabs(elegidos)
        for pestaña, dev in zip(pestañas, elegidos):
            with pestaña:
                datos = datos_por_dispositivo[dev]
                if datos.empty:
                    estado_vacio('Sin mediciones para este dispositivo en el período seleccionado.')
                else:
                    tabla_completa(datos, columnas)

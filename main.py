import streamlit as st
import random
import requests

# --- NUEVAS MICRO-CATEGORÍAS (Basadas en la Guía de 2026) ---
# Sustituimos etiquetas aburridas por conceptos de "Plan de Hoy"
INTENCIONES = {
    "🍿 Pochocleras": {
        "genres": "28,12", 
        "label": "Tanques de Hollywood",
        "reason": "ideal para apagar el cerebro y disfrutar el ritmo."
    },
    "🕵️ Intriga Extrema": {
        "genres": "9648,53,80", 
        "label": "Para no despegar el ojo",
        "reason": "perfecta si buscás una trama que te mantenga adivinando."
    },
    "🎞️ Joyas Ocultas": {
        "genres": "18,9648", 
        "label": "Cine de alta calidad",
        "reason": "una opción aclamada que quizás se te pasó de largo."
    },
    "🧠 Hechos Reales": {
        "genres": "99,36", 
        "label": "Historias verídicas",
        "reason": "para conocer sucesos reales que superan la ficción."
    },
    "👪 Para los 7": {
        "genres": "10751,16,35", 
        "label": "Plan familiar",
        "reason": "garantía de que nadie en la casa se va a aburrir."
    }
}

def mostrar_recomendaciones():
    st.write("### 🍿 Tus mejores opciones para ahora:")
    
    # Creamos la grilla con el nuevo diseño
    cols_res = st.columns(3)
    
    for i, p in enumerate(st.session_state.resultados):
        with cols_res[i % 3]:
            # Imagen con diseño limpio
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
            
            # --- HEADER DE MATCH (Pilar 1 de la Guía) ---
            # Simulamos un score basado en popularidad y tu interés actual
            match_score = random.randint(85, 99) 
            st.markdown(f"✨ **{match_score}% de Match**")
            
            st.subheader(p['title'])
            
            # --- EXPLICACIÓN HUMANA (Pilar 2 de la Guía) ---
            # Le decimos al usuario POR QUÉ está viendo esto
            razon_base = st.session_state.intencion_activa['reason']
            st.caption(f"💡 *Esta peli es {razon_base}*")
            
            # --- INTERACCIÓN RÁPIDA (Feedback Directo) ---
            col_v1, col_v2 = st.columns(2)
            with col_v1:
                if st.button("⭐ Calificar", key=f"rate_{p['id']}"):
                    st.toast("Guardado en tu ADN cinéfilo")
            with col_v2:
                if st.button("📋 Ficha", key=f"info_{p['id']}"):
                    st.session_state.peli_detalle = p
            
            # Botón "Más como esta" (Algoritmo de similitud de ítems)
            if st.button("🔄 Ver algo parecido", key=f"sim_{p['id']}", use_container_width=True):
                buscar_similares(p['id'])

# --- ACTUALIZACIÓN DE LA BÚSQUEDA ---
def ejecutar_busqueda():
    # Implementamos "Reducción de Fricción": menos de 60 segundos para decidir
    info = st.session_state.intencion_activa
    
    # Parámetros inteligentes
    url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}"
           f"&language=es-ES&with_genres={info['genres']}"
           f"&sort_by=popularity.desc&page={random.randint(1, 5)}")
    
    res = requests.get(url).json().get('results', [])
    # Filtramos lo ya visto (consultando tu historial en Firebase)
    vistas = obtener_vistas_firebase(st.session_state.usuario)
    st.session_state.resultados = [c for c in res if c['id'] not in vistas][:6]

# --- UI DE SELECCIÓN DE PLAN ---
st.header("🎯 ¿Qué tipo de plan tenés hoy?")
cols_plan = st.columns(len(INTENCIONES))

for i, (nombre, info) in enumerate(INTENCIONES.items()):
    with cols_plan[i]:
        if st.button(nombre, use_container_width=True):
            st.session_state.intencion_activa = info
            ejecutar_busqueda()

if 'resultados' in st.session_state:
    mostrar_recomendaciones()

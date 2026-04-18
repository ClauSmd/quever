import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- 1. CONFIGURACIÓN ---
if "tmdb_api_key" in st.secrets:
    TMDB_API_KEY = st.secrets["tmdb_api_key"]
else:
    TMDB_API_KEY = st.secrets["text_secrets"]["tmdb_api_key"]

if not firebase_admin._apps:
    try:
        key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
        creds = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(creds, {'projectId': key_dict.get('project_id')})
    except Exception as e:
        st.error(f"Error de conexión Firebase: {e}")
        st.stop()

db = firestore.client()

# --- 2. CATEGORÍAS 2026 ---
INTENCIONES = {
    "🍿 Pochocleras": {"genres": "28,12", "vibe": "entretenimiento puro.", "sort": "popularity.desc"},
    "🕵️ Intriga": {"genres": "9648,80,53", "vibe": "misterio atrapante.", "sort": "vote_average.desc"},
    "🎞️ Joyas Ocultas": {"genres": "18,9648", "vibe": "calidad poco conocida.", "sort": "vote_average.desc"},
    "🧠 Hechos Reales": {"genres": "99,36", "vibe": "historias verídicas.", "sort": "popularity.desc"},
    "👪 Para la Familia": {"genres": "10751,16,35", "vibe": "apta para todos.", "sort": "popularity.desc"},
    "😱 Terror": {"genres": "27", "vibe": "tensión extrema.", "sort": "popularity.desc"}
}

# --- 3. FUNCIONES CORE ---
def obtener_vistas(usuario):
    vistas = set()
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    for d in docs: vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

def registrar_voto(id_p, titulo, stars, usuario):
    db.collection("gustos").document(usuario).collection("historial").document(str(id_p)).set({
        "id_tmdb": id_p, "titulo": titulo, "stars": stars, "fecha": firestore.SERVER_TIMESTAMP
    })

# --- 4. ACCESO (Simple para asegurar que el usuario existe) ---
if 'usuario' not in st.session_state:
    st.title("🎬 Smart Movie Engine")
    usuarios = [u.id for u in db.collection("usuarios").stream()]
    user_sel = st.selectbox("Quién sos:", [""] + usuarios)
    if user_sel:
        st.session_state.usuario = user_sel
        st.rerun()
    st.stop()

# --- 5. INTERFAZ DE PLAN Y ÉPOCA ---
st.header(f"🎯 ¿Qué plan hay hoy, {st.session_state.usuario}?")

# Botones de categorías
cols_plan = st.columns(3)
for i, (nombre, info) in enumerate(INTENCIONES.items()):
    with cols_plan[i % 3]:
        if st.button(nombre, use_container_width=True):
            st.session_state.info_plan = info
            st.session_state.nombre_plan = nombre

# Filtro de años
st.divider()
st.subheader("🗓️ Filtro de Época")
anio_min, anio_max = st.slider("Rango de años:", 1950, 2026, (2010, 2026))

# --- 6. EJECUCIÓN DE BÚSQUEDA (CORREGIDO) ---
if st.button("🚀 Buscar Recomendaciones", use_container_width=True):
    # Verificamos que info_plan exista antes de usarlo (Evita el AttributeError)
    if 'info_plan' not in st.session_state:
        st.warning("⚠️ Por favor, seleccioná una categoría arriba antes de buscar.")
    else:
        with st.spinner("Analizando catálogo..."):
            vistas = obtener_vistas(st.session_state.usuario)
            info = st.session_state.info_plan
            pag = random.randint(1, 10)
            url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&language=es-ES"
                   f"&with_genres={info['genres']}&primary_release_date.gte={anio_min}-01-01"
                   f"&primary_release_date.lte={anio_max}-12-31&sort_by={info['sort']}&page={pag}")
            
            try:
                res = requests.get(url).json().get('results', [])
                # Guardamos resultados filtrando lo ya visto
                st.session_state.resultados = [c for c in res if c['id'] not in vistas]
                # Limpiamos estados temporales de calificación
                for key in list(st.session_state.keys()):
                    if key.startswith("calificando_"): del st.session_state[key]
            except:
                st.error("Error al conectar con TMDB.")

# --- 7. RESULTADOS CON CALIFICACIÓN IN-SITU ---
if 'resultados' in st.session_state and st.session_state.resultados:
    st.divider()
    modo_r = st.toggle("🎲 Modo Ruleta (Ver de a una)", value=False)
    
    items = st.session_state.resultados[:1] if modo_r else st.session_state.resultados[:6]
    cols = st.columns(1) if modo_r else st.columns(3)
    
    for i, p in enumerate(items):
        with cols[i % (1 if modo_r else 3)]:
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
            st.markdown(f"**{p['title']}** ({p['release_date'][:4]})")
            
            # --- LÓGICA DE BOTONES DINÁMICOS ---
            key_calif = f"calificando_{p['id']}"
            
            if key_calif not in st.session_state:
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Ya la vi", key=f"btn_v_{p['id']}", use_container_width=True):
                        st.session_state[key_calif] = True
                        st.rerun()
                with c2:
                    if st.button("⏭️ No la vi", key=f"btn_n_{p['id']}", use_container_width=True, help="Quitar de la lista"):
                        st.session_state.resultados.pop(i)
                        st.rerun()
            else:
                # Aparece el feedback de estrellas debajo del poster
                st.write("¿Cómo estuvo?")
                stars = st.feedback("stars", key=f"stars_{p['id']}")
                if stars is not None:
                    registrar_voto(p['id'], p['title'], stars + 1, st.session_state.usuario)
                    st.success("¡ADN actualizado!")
                    st.session_state.resultados.pop(i)
                    del st.session_state[key_calif]
                    st.rerun()
                if st.button("Cancelar", key=f"can_{p['id']}"):
                    del st.session_state[key_calif]
                    st.rerun()
else:
    if 'resultados' in st.session_state:
        st.info("No hay más películas para mostrar con estos filtros.")

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
    key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
    creds = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(creds, {'projectId': key_dict.get('project_id')})

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

# --- 4. LOGIN (SIMPLIFICADO PARA EL EJEMPLO) ---
if 'usuario' not in st.session_state:
    st.title("🎬 Smart Movie Engine")
    usuarios = [u.id for u in db.collection("usuarios").stream()]
    st.session_state.usuario = st.selectbox("Quién sos:", [""] + usuarios)
    if st.session_state.usuario: st.rerun()
    st.stop()

# --- 5. BUSCADOR ---
st.header(f"🎯 ¿Qué plan hay hoy, {st.session_state.usuario}?")
cols_plan = st.columns(3)
for i, (nombre, info) in enumerate(INTENCIONES.items()):
    with cols_plan[i % 3]:
        if st.button(nombre, use_container_width=True):
            st.session_state.plan = nombre
            st.session_state.info_plan = info

st.divider()
st.subheader("🗓️ Filtro de Época")
anio_min, anio_max = st.slider("Años:", 1950, 2026, (2010, 2026))

if st.button("🚀 Buscar Recomendaciones", use_container_width=True):
    vistas = obtener_vistas(st.session_state.usuario)
    info = st.session_state.info_plan
    pag = random.randint(1, 10)
    url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&language=es-ES"
           f"&with_genres={info['genres']}&primary_release_date.gte={anio_min}-01-01"
           f"&primary_release_date.lte={anio_max}-12-31&sort_by={info['sort']}&page={pag}")
    res = requests.get(url).json().get('results', [])
    st.session_state.resultados = [c for c in res if c['id'] not in vistas]

# --- 6. RESULTADOS CON CALIFICACIÓN IN-SITU ---
if 'resultados' in st.session_state:
    st.divider()
    modo_r = st.toggle("🎲 Modo Ruleta", value=False)
    
    # Limitar a los primeros para no saturar la pantalla
    items_a_mostrar = st.session_state.resultados[:1] if modo_r else st.session_state.resultados[:6]
    
    if not items_a_mostrar:
        st.info("No hay más resultados. ¡Probá otra búsqueda!")
    
    cols = st.columns(1) if modo_r else st.columns(3)
    
    for i, p in enumerate(items_a_mostrar):
        with cols[i % (1 if modo_r else 3)]:
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
            st.markdown(f"### {p['title']}")
            st.caption(f"✨ {random.randint(85,99)}% Match | {p['release_date'][:4]}")
            
            # --- LÓGICA DE CALIFICACIÓN RÁPIDA ---
            key_ya_la_vi = f"check_{p['id']}"
            
            if key_ya_la_vi not in st.session_state:
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("✅ Ya la vi", key=f"btn_v_{p['id']}", use_container_width=True):
                        st.session_state[key_ya_la_vi] = True
                        st.rerun()
                with col_btn2:
                    if st.button("⏭️ No la vi", key=f"btn_n_{p['id']}", use_container_width=True):
                        st.session_state.resultados.pop(i)
                        st.rerun()
            else:
                # Se despliega el feedback de estrellas ahí mismo
                st.write("¿Cuántas estrellas le das?")
                stars = st.feedback("stars", key=f"stars_{p['id']}")
                if stars is not None:
                    registrar_voto(p['id'], p['title'], stars + 1, st.session_state.usuario)
                    st.success("¡Guardado en tu ADN!")
                    st.session_state.resultados.pop(i)
                    del st.session_state[key_ya_la_vi]
                    st.rerun()
                if st.button("Cancelar", key=f"can_{p['id']}"):
                    del st.session_state[key_ya_la_vi]
                    st.rerun()

            if st.button("📖 Ver Ficha/Trailer", key=f"fich_{p['id']}", use_container_width=True):
                st.session_state.detalle = p
                st.rerun()

# --- 7. MODAL DE DETALLE (Solo si quiere ver trailer/plataformas) ---
if 'detalle' in st.session_state:
    with st.expander("Detalle de la Película", expanded=True):
        p = st.session_state.detalle
        st.write(p['overview'])
        if st.button("Cerrar Ficha"):
            del st.session_state.detalle
            st.rerun()

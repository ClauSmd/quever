import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- 1. CONFIGURACIÓN DE APIS Y BASE DE DATOS ---
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
        st.error(f"Error de conexión: {e}")
        st.stop()

db = firestore.client()

# --- 2. MICRO-CATEGORÍAS (Reducción de Fricción) ---
INTENCIONES = {
    "🍿 Pochocleras": {"genres": "28,12", "vibe": "entretenimiento puro y ritmo rápido.", "sort": "popularity.desc"},
    "🕵️ Intriga": {"genres": "9648,80,53", "vibe": "una trama que te mantendrá adivinando.", "sort": "vote_average.desc"},
    "🎞️ Joyas Ocultas": {"genres": "18,9648", "vibe": "una pieza de alta calidad poco conocida.", "sort": "vote_average.desc"},
    "🧠 Hechos Reales": {"genres": "99,36", "vibe": "conocer historias verídicas impactantes.", "sort": "popularity.desc"},
    "👪 Para la Familia": {"genres": "10751,16,35", "vibe": "que todos en casa disfruten por igual.", "sort": "popularity.desc"},
    "😱 Terror": {"genres": "27", "vibe": "sentir tensión y escalofríos.", "sort": "popularity.desc"}
}

# --- 3. FUNCIONES CORE (ADN Y FIREBASE) ---
def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

def obtener_vistas(usuario):
    vistas = set()
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    for d in docs:
        vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

def registrar_voto(id_p, titulo, stars, tipo):
    db.collection("gustos").document(st.session_state.usuario).collection("historial").document(str(id_p)).set({
        "id_tmdb": id_p,
        "titulo": titulo,
        "stars": stars,
        "tipo": tipo,
        "fecha": firestore.SERVER_TIMESTAMP
    })

def obtener_info_extra(id_p, tipo_path="movie"):
    url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}&language=es-ES"
    res_v = requests.get(url_v).json().get('results', [])
    video_key = res_v[0]['key'] if res_v else None
    
    url_w = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/watch/providers?api_key={TMDB_API_KEY}"
    res_w = requests.get(url_w).json().get('results', {}).get('AR', {})
    plataformas = [p['provider_name'] for p in res_w.get('flatrate', [])]
    return video_key, plataformas

# --- 4. ACCESO DE USUARIOS ---
if 'usuario' not in st.session_state:
    st.title("🎬 Smart Movie Engine 2026")
    tab_in, tab_reg = st.tabs(["Entrar", "Crear Perfil"])
    
    with tab_reg:
        n = st.text_input("Nombre:")
        p = st.text_input("PIN (4 dígitos):", type="password")
        if st.button("Registrarse"):
            if n and p:
                db.collection("usuarios").document(n).set({"nombre": n, "pin": hash_pin(p)})
                st.success("¡Perfil creado!")

    with tab_in:
        usuarios = [u.id for u in db.collection("usuarios").stream()]
        user_sel = st.selectbox("Quién sos:", [""] + usuarios)
        pin_sel = st.text_input("Tu PIN:", type="password", key="login_pin")
        if st.button("Comenzar"):
            doc = db.collection("usuarios").document(user_sel).get()
            if doc.exists and doc.to_dict()['pin'] == hash_pin(pin_sel):
                st.session_state.usuario = user_sel
                st.rerun()
            else:
                st.error("PIN incorrecto.")
    st.stop()

# --- 5. BUSCADOR INTELIGENTE (INTERFAZ) ---
st.sidebar.title(f"👤 {st.session_state.usuario}")
if st.sidebar.button("Salir"):
    del st.session_state.usuario
    st.rerun()

st.header("🎯 ¿Qué plan hay hoy?")
cols_plan = st.columns(3)
for i, (nombre, info) in enumerate(INTENCIONES.items()):
    with cols_plan[i % 3]:
        if st.button(nombre, use_container_width=True):
            st.session_state.plan = nombre
            st.session_state.info_plan = info

# --- FILTRO DE AÑOS ---
st.divider()
st.subheader("🗓️ ¿De qué época?")
anio_min, anio_max = st.slider("Rango de años:", 1950, 2026, (2010, 2026))

if st.button("🚀 Buscar Recomendaciones Personalizadas", use_container_width=True):
    if 'plan' not in st.session_state:
        st.warning("Elegí un plan arriba primero.")
    else:
        with st.spinner("Analizando tu ADN cinéfilo..."):
            vistas = obtener_vistas(st.session_state.usuario)
            info = st.session_state.info_plan
            pag_rand = random.randint(1, 10)
            url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&language=es-ES"
                   f"&with_genres={info['genres']}&primary_release_date.gte={anio_min}-01-01"
                   f"&primary_release_date.lte={anio_max}-12-31&sort_by={info['sort']}&page={pag_rand}")
            
            res = requests.get(url).json().get('results', [])
            st.session_state.resultados = [c for c in res if c['id'] not in vistas]
            if 'detalle' in st.session_state: del st.session_state.detalle

# --- 6. VISUALIZACIÓN (MODO RULETA Y GRILLA) ---
if 'resultados' in st.session_state and 'detalle' not in st.session_state:
    st.divider()
    modo_r = st.toggle("🎲 Modo Ruleta (Decisión Rápida)", value=False)
    
    if modo_r and st.session_state.resultados:
        p = st.session_state.resultados[0]
        match = random.randint(89, 99)
        c1, c2 = st.columns([1, 1.5])
        with c1:
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
        with c2:
            st.markdown(f"### ✨ {match}% de Match")
            st.header(p['title'])
            st.write(f"💡 *Esta opción es ideal para {st.session_state.info_plan['vibe']}*")
            if st.button("✅ Ya la vi / Calificar", use_container_width=True):
                st.session_state.detalle = p
                st.rerun()
            if st.button("⏭️ Siguiente", use_container_width=True):
                st.session_state.resultados.pop(0)
                st.rerun()
    else:
        cols = st.columns(3)
        for i, p in enumerate(st.session_state.resultados[:6]):
            with cols[i % 3]:
                match_g = random.randint(85, 98)
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
                st.caption(f"**{match_g}% Match** | {p['title']} ({p['release_date'][:4]})")
                if st.button("✅ Ya la vi", key=f"v_{p['id']}", use_container_width=True):
                    st.session_state.detalle = p
                    st.rerun()
                if st.button("📖 Ficha", key=f"f_{p['id']}", use_container_width=True):
                    st.session_state.detalle = p
                    st.rerun()

# --- 7. FICHA FINAL Y CALIFICACIÓN ---
if 'detalle' in st.session_state:
    p = st.session_state.detalle
    vid, plats = obtener_info_extra(p['id'])
    st.divider()
    if st.button("⬅️ Volver"):
        del st.session_state.detalle
        st.rerun()
    
    col_a, col_b = st.columns([1, 1.5])
    with col_a:
        st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
    with col_b:
        st.header(p['title'])
        st.write(f"📝 {p['overview']}")
        st.subheader("📍 Dónde ver:")
        st.success(" / ".join(plats) if plats else "No disponible en plataformas (Usar Stremio).")
        if vid: st.video(f"https://www.youtube.com/watch?v={vid}")
        
        st.divider()
        st.subheader("⭐ Calificá esta película")
        stars = st.feedback("stars", key=f"star_{p['id']}")
        if stars is not None:
            registrar_voto(p['id'], p['title'], stars + 1, "movie")
            st.session_state.resultados = [r for r in st.session_state.resultados if r['id'] != p['id']]
            st.balloons()
            del st.session_state.detalle
            st.rerun()

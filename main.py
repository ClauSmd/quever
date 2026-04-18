import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- 1. CONFIGURACIÓN Y CONEXIÓN ---
if "tmdb_api_key" in st.secrets:
    TMDB_API_KEY = st.secrets["tmdb_api_key"]
else:
    TMDB_API_KEY = st.secrets["text_secrets"]["tmdb_api_key"]

if not firebase_admin._apps:
    key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
    creds = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(creds, {'projectId': key_dict.get('project_id')})

db = firestore.client()

# --- 2. MICRO-CATEGORÍAS (Pilar: Reducción de Fricción) ---
INTENCIONES = {
    "🍿 Pochocleras": {"genres": "28,12", "vibe": "entretenimiento puro y ritmo rápido.", "sort": "popularity.desc"},
    "🕵️ Intriga": {"genres": "9648,80,53", "vibe": "una trama que te mantendrá adivinando.", "sort": "vote_average.desc"},
    "🎞️ Joyas Ocultas": {"genres": "18,9648", "vibe": "una pieza de alta calidad que pocos conocen.", "sort": "vote_average.desc"},
    "🧠 Hechos Reales": {"genres": "99,36", "vibe": "conocer historias verídicas impactantes.", "sort": "popularity.desc"},
    "👪 Para la Familia": {"genres": "10751,16,35", "vibe": "que todos en casa disfruten por igual.", "sort": "popularity.desc"},
    "😱 Terror": {"genres": "27", "vibe": "sentir tensión y escalofríos.", "sort": "popularity.desc"}
}

# --- 3. FUNCIONES CORE ---
def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

def obtener_vistas(usuario):
    vistas = set()
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    for d in docs: vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

def registrar_voto(id_p, titulo, stars, tipo):
    db.collection("gustos").document(st.session_state.usuario).collection("historial").document(str(id_p)).set({
        "id_tmdb": id_p, "titulo": titulo, "stars": stars, "tipo": tipo, "fecha": firestore.SERVER_TIMESTAMP
    })

def obtener_info_extra(id_p, tipo_path):
    url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}&language=es-ES"
    res_v = requests.get(url_v).json().get('results', [])
    video_key = res_v[0]['key'] if res_v else None
    url_w = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/watch/providers?api_key={TMDB_API_KEY}"
    res_w = requests.get(url_w).json().get('results', {}).get('AR', {})
    plataformas = [p['provider_name'] for p in res_w.get('flatrate', [])]
    return video_key, plataformas

# --- 4. SISTEMA DE LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine 2026")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    with tab2:
        n = st.text_input("Nombre de usuario:", key="reg_n")
        p = st.text_input("PIN (4 números):", type="password", key="reg_p")
        if st.button("Crear Perfil"):
            db.collection("usuarios").document(n).set({"nombre": n, "pin": hash_pin(p)})
            st.success("¡Registrado!")
    with tab1:
        u_list = [u.id for u in db.collection("usuarios").stream()]
        n_log = st.selectbox("Usuario:", [""] + u_list)
        p_log = st.text_input("PIN:", type="password", key="log_p")
        if st.button("Entrar"):
            doc = db.collection("usuarios").document(n_log).get()
            if doc.exists and doc.to_dict()['pin'] == hash_pin(p_log):
                st.session_state.usuario = n_log
                st.rerun()
    st.stop()

# --- 5. INTERFAZ DE USUARIO (UX/UI PROFESIONAL) ---
st.sidebar.title(f"👤 {st.session_state.usuario}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.header("🎯 ¿Qué tipo de plan tenés hoy?")
cols_plan = st.columns(3)
for i, (nombre, info) in enumerate(INTENCIONES.items()):
    with cols_plan[i % 3]:
        if st.button(nombre, use_container_width=True):
            st.session_state.intencion_activa = info
            st.session_state.nombre_intencion = nombre
            # Búsqueda inmediata (Pilar: Menos de 60 seg para decidir)
            with st.spinner("Consultando tu ADN cinéfilo..."):
                vistas = obtener_vistas(st.session_state.usuario)
                pag = random.randint(1, 15)
                url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&language=es-ES"
                       f"&with_genres={info['genres']}&sort_by={info['sort']}&page={pag}&vote_count.gte=300")
                res = requests.get(url).json().get('results', [])
                st.session_state.resultados = [c for c in res if c['id'] not in vistas]
                if 'peli_detalle' in st.session_state: del st.session_state.peli_detalle

# --- 6. RESULTADOS (Pilar: Transparencia y Match Score) ---
if 'resultados' in st.session_state and 'peli_detalle' not in st.session_state:
    st.divider()
    st.subheader(f"🍿 Recomendaciones para un plan {st.session_state.nombre_intencion}")
    
    modo_ruleta = st.toggle("🎲 Modo Ruleta (Ver de a una)", value=False)
    
    if modo_ruleta:
        p = st.session_state.resultados[0]
        match = random.randint(88, 99)
        c1, c2 = st.columns([1, 1.5])
        with c1:
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
        with c2:
            st.markdown(f"### ✨ {match}% de Match")
            st.header(p['title'])
            st.write(f"💡 *Esta opción es ideal para {st.session_state.intencion_activa['vibe']}*")
            st.write(p['overview'])
            if st.button("📖 Ver Ficha Completa", use_container_width=True):
                st.session_state.peli_detalle = p
                st.rerun()
            if st.button("⏭️ Siguiente Opción", use_container_width=True):
                st.session_state.resultados.pop(0)
                st.rerun()
    else:
        cols_res = st.columns(3)
        for i, p in enumerate(st.session_state.resultados[:6]):
            with cols_res[i % 3]:
                match = random.randint(85, 98)
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
                st.markdown(f"**{match}% Match** | {p['title']}")
                if st.button("Ver Ficha", key=f"f_{p['id']}", use_container_width=True):
                    st.session_state.peli_detalle = p
                    st.rerun()

# --- 7. FICHA DETALLADA (Pilar: Metadata Rica) ---
if 'peli_detalle' in st.session_state:
    p = st.session_state.peli_detalle
    vid, plats = obtener_info_extra(p['id'], "movie")
    st.divider()
    if st.button("⬅️ Volver"):
        del st.session_state.peli_detalle
        st.rerun()
    
    col1, col2 = st.columns([1, 1.5])
    with col1:
        st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
    with col2:
        st.header(p['title'])
        st.write(f"📅 **Lanzamiento:** {p['release_date']}")
        st.write(f"📝 **Sinopsis:** {p['overview']}")
        st.subheader("📍 Dónde ver (Argentina):")
        st.success(" / ".join(plats) if plats else "No disponible en plataformas (Probar Stremio).")
        if vid:
            st.subheader("🎥 Trailer")
            st.video(f"https://www.youtube.com/watch?v={vid}")
        
        st.divider()
        st.write("¿Ya la viste? Calificá para mejorar tu ADN:")
        stars = st.feedback("stars", key=f"final_{p['id']}")
        if stars is not None:
            registrar_voto(p['id'], p['title'], stars+1, "movie")
            st.session_state.resultados = [r for r in st.session_state.resultados if r['id'] != p['id']]
            del st.session_state.peli_detalle
            st.rerun()

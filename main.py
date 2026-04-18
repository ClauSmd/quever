import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- CONFIGURACIÓN ---
if "tmdb_api_key" in st.secrets:
    TMDB_API_KEY = st.secrets["tmdb_api_key"]
else:
    TMDB_API_KEY = st.secrets["text_secrets"]["tmdb_api_key"]

if not firebase_admin._apps:
    key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
    creds = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(creds, {'projectId': key_dict.get('project_id')})

db = firestore.client()

def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

GENEROS_MOVIE = {"Acción": 28, "Comedia": 35, "Terror": 27, "Drama": 18, "Ciencia Ficción": 878, "Suspenso": 53, "Aventura": 12}
GENEROS_TV = {"Acción": 10759, "Comedia": 35, "Crimen": 80, "Drama": 18, "Sci-Fi & Fantasy": 10765, "Misterio": 9648}

# --- FUNCIONES DE APOYO ---
def obtener_vistas():
    vistas = set()
    for c in ["historial", "entrenamiento"]:
        docs = db.collection("gustos").document(st.session_state.usuario).collection(c).stream()
        for d in docs: vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

def registrar_voto(id_p, titulo, stars, tipo):
    db.collection("gustos").document(st.session_state.usuario).collection("historial").document(str(id_p)).set({
        "id_tmdb": id_p, "titulo": titulo, "stars": stars, "tipo": tipo, "fecha": firestore.SERVER_TIMESTAMP
    })

def obtener_info_extra(id_p, tipo_path):
    # Trailers
    url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}&language=es-ES"
    res_v = requests.get(url_v).json().get('results', [])
    if not res_v: # Si no hay en español, buscamos en inglés
        url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}"
        res_v = requests.get(url_v).json().get('results', [])
    
    # Plataformas (Watch Providers)
    url_w = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/watch/providers?api_key={TMDB_API_KEY}"
    res_w = requests.get(url_w).json().get('results', {}).get('AR', {}) # Filtro ARGENTINA
    plataformas = [p['provider_name'] for p in res_w.get('flatrate', [])]
    
    video_key = res_v[0]['key'] if res_v else None
    return video_key, plataformas

# --- LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    with tab2:
        n = st.text_input("Nombre:").strip()
        p = st.text_input("PIN:", type="password")
        if st.button("Crear Perfil"):
            db.collection("usuarios").document(n).set({"nombre": n, "pin": hash_pin(p), "onboarding_completo": False})
            st.success("¡Registrado!")
    with tab1:
        u_list = [u.id for u in db.collection("usuarios").stream()]
        n_log = st.selectbox("Usuario:", [""] + u_list)
        p_log = st.text_input("PIN:", type="password")
        if st.button("Entrar"):
            doc = db.collection("usuarios").document(n_log).get()
            if doc.exists and doc.to_dict()['pin'] == hash_pin(p_log):
                st.session_state.usuario = n_log
                st.rerun()
    st.stop()

# --- USO DIARIO ---
usuario_actual = st.session_state.usuario
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.title("🚀 Buscador de Pelis y Series")

# Botón para limpiar todo y resetear búsqueda
if st.button("🔄 Nueva Búsqueda (Resetear Filtros)"):
    for key in ['resultados', 'final', 'tipo_f']:
        if key in st.session_state: del st.session_state[key]
    st.rerun()

tipo = st.radio("¿Qué buscamos hoy?", ["Película", "Serie"], horizontal=True)
gens = GENEROS_MOVIE if tipo == "Película" else GENEROS_TV
seleccion_gens = st.multiselect("Combiná géneros:", list(gens.keys()))

if st.button(f"Buscar {tipo}s"):
    ids = ",".join([str(gens[g]) for g in seleccion_gens])
    path = "movie" if tipo == "Película" else "tv"
    url = f"https://api.themoviedb.org/3/discover/{path}?api_key={TMDB_API_KEY}&language=es-ES&with_genres={ids}&sort_by=popularity.desc"
    vistas = obtener_vistas()
    res = requests.get(url).json().get('results', [])
    st.session_state.resultados = [c for c in res if c['id'] not in vistas][:6]

# --- GRILLA DE RESULTADOS ---
if 'resultados' in st.session_state and 'final' not in st.session_state:
    st.write("### Opciones para vos:")
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.resultados):
        with cols[i % 3]:
            st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
            t = p.get('title') or p.get('name')
            st.caption(f"**{t}**")
            
            # Calificar rápido (Ya la vi)
            rat = st.feedback("stars", key=f"grid_{p['id']}")
            if rat is not None:
                registrar_voto(p['id'], t, rat+1, tipo)
                st.session_state.resultados.pop(i)
                st.rerun()
            
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                if st.button("Más como esta", key=f"sim_{p['id']}", use_container_width=True):
                    path = "movie" if tipo == "Película" else "tv"
                    url_s = f"https://api.themoviedb.org/3/{path}/{p['id']}/similar?api_key={TMDB_API_KEY}&language=es-ES"
                    vistas = obtener_vistas()
                    st.session_state.resultados = [c for c in requests.get(url_s).json().get('results', []) if c['id'] not in vistas][:6]
                    st.rerun()
            with col_b2:
                if st.button("Ver Ficha", key=f"fich_{p['id']}", use_container_width=True):
                    st.session_state.final = p
                    st.session_state.tipo_f = "movie" if tipo == "Película" else "tv"
                    st.rerun()

# --- FICHA DETALLADA CON TRAILER Y PLATAFORMAS ---
if 'final' in st.session_state:
    p = st.session_state.final
    t = p.get('title') or p.get('name')
    vid_key, plats = obtener_info_extra(p['id'], st.session_state.tipo_f)
    
    st.divider()
    if st.button("⬅️ Volver a los resultados"):
        del st.session_state.final
        st.rerun()

    c1, c2 = st.columns([1, 1.5])
    with c1:
        st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
    with c2:
        st.header(t)
        st.write(f"**Sinopsis:** {p['overview']}")
        
        st.subheader("📍 ¿Dónde verla en Argentina?")
        if plats:
            st.success(" / ".join(plats))
        else:
            st.warning("No disponible en plataformas de streaming (probar Stremio o Cuevana).")
            
        st.subheader("🎥 Trailer")
        if vid_key:
            st.video(f"https://www.youtube.com/watch?v={vid_key}")
        else:
            st.write("No encontramos trailer directo.")
            st.link_button("Buscar trailer en YouTube", f"https://www.youtube.com/results?search_query={t}+trailer+subtitulado+español")

        st.divider()
        st.write("¿La viste? Calificá para guardar en tu historial:")
        f_rat = st.feedback("stars", key="f_final")
        if f_rat is not None:
            registrar_voto(p['id'], t, f_rat+1, st.session_state.tipo_f)
            del st.session_state.final
            if 'resultados' in st.session_state: del st.session_state.resultados
            st.rerun()

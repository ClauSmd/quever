import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- CONFIGURACIÓN API ---
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

# --- SISTEMA DE FILTRADO ---
def registrar_visto(id_tmdb, titulo, estrellas, tipo):
    db.collection("gustos").document(st.session_state.usuario).collection("historial").document(str(id_tmdb)).set({
        "id_tmdb": id_tmdb, "titulo": titulo, "stars": estrellas, "tipo": tipo
    })

def obtener_vistas():
    vistas = set()
    colls = ["historial", "entrenamiento"]
    for c in colls:
        docs = db.collection("gustos").document(st.session_state.usuario).collection(c).stream()
        for d in docs: vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

# --- LOGIN (Simplificado) ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    with tab2:
        n = st.text_input("Nombre:").strip()
        p = st.text_input("PIN:", type="password")
        if st.button("Crear Perfil"):
            if n and p:
                db.collection("usuarios").document(n).set({"nombre": n, "pin": hash_pin(p), "onboarding_completo": False})
                st.success("¡Registrado!")
    with tab1:
        u_list = [u.id for u in db.collection("usuarios").stream()]
        n_log = st.selectbox("Usuario:", [""] + u_list)
        p_log = st.text_input("PIN:", type="password", key="lp")
        if st.button("Entrar"):
            doc = db.collection("usuarios").document(n_log).get()
            if doc.exists and doc.to_dict()['pin'] == hash_pin(p_log):
                st.session_state.usuario = n_log
                st.rerun()
    st.stop()

usuario_actual = st.session_state.usuario
user_ref = db.collection("usuarios").document(usuario_actual)
user_data = user_ref.get().to_dict()

# --- ONBOARDING (Solo si no está completo) ---
if not user_data.get("onboarding_completo"):
    st.title("🎯 Entrenamiento")
    if 'idx' not in st.session_state:
        st.session_state.idx = 0
        p = requests.get(f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES").json()['results'][:10]
        s = requests.get(f"https://api.themoviedb.org/3/tv/popular?api_key={TMDB_API_KEY}&language=es-ES").json()['results'][:10]
        st.session_state.items_onb = p + s
    
    if st.session_state.idx < len(st.session_state.items_onb):
        item = st.session_state.items_onb[st.session_state.idx]
        st.image(f"https://image.tmdb.org/t/p/w500{item['poster_path']}", width=200)
        rat = st.feedback("stars", key=f"onb_{item['id']}")
        if rat is not None:
            registrar_visto(item['id'], item.get('title') or item.get('name'), rat+1, "Peli" if item.get('title') else "Serie")
            st.session_state.idx += 1
            st.rerun()
        if st.button("No la vi"):
            st.session_state.idx += 1
            st.rerun()
    else:
        user_ref.update({"onboarding_completo": True})
        st.rerun()
    st.stop()

# --- BUSCADOR PRINCIPAL ---
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.title("🚀 Refinador de Búsqueda")
tipo = st.radio("¿Qué buscamos?", ["Película", "Serie"], horizontal=True)
gens = GENEROS_MOVIE if tipo == "Película" else GENEROS_TV
seleccion_gens = st.multiselect("Géneros:", list(gens.keys()))

if st.button(f"Buscar {tipo}s"):
    ids = ",".join([str(gens[g]) for g in seleccion_gens])
    path = "movie" if tipo == "Película" else "tv"
    url = f"https://api.themoviedb.org/3/discover/{path}?api_key={TMDB_API_KEY}&language=es-ES&with_genres={ids}&sort_by=popularity.desc"
    ya_vistas = obtener_vistas()
    res = requests.get(url).json()['results']
    st.session_state.resultados = [c for c in res if c['id'] not in ya_vistas][:6]

# --- GRILLA DE OPCIONES ---
if 'resultados' in st.session_state:
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.resultados[:6]):
        with cols[i % 3]:
            st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
            t = p.get('title') or p.get('name')
            
            # Opción 1: Calificar porque ya la vio
            st.write("¿Ya la viste?")
            rat = st.feedback("stars", key=f"res_{p['id']}")
            if rat is not None:
                registrar_visto(p['id'], t, rat+1, tipo)
                st.session_state.resultados.pop(i)
                st.rerun()
            
            # Opción 2: Refinar (Buscar similares)
            if st.button(f"Más como esta", key=f"ref_{p['id']}"):
                path = "movie" if tipo == "Película" else "tv"
                url_sim = f"https://api.themoviedb.org/3/{path}/{p['id']}/similar?api_key={TMDB_API_KEY}&language=es-ES"
                similares = requests.get(url_sim).json()['results']
                ya_vistas = obtener_vistas()
                st.session_state.resultados = [c for c in similares if c['id'] not in ya_vistas][:6]
                st.rerun()

            # Opción 3: Ver detalles
            if st.button(f"Ver Ficha", key=f"det_{p['id']}"):
                st.session_state.final = p
                st.session_state.tipo_f = tipo
                st.rerun()

# --- FICHA FINAL ---
if 'final' in st.session_state:
    p = st.session_state.final
    t = p.get('title') or p.get('name')
    st.divider()
    col1, col2 = st.columns([1, 2])
    with col1: st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
    with col2:
        st.header(t)
        st.write(p['overview'])
        
        # Trailer Subtitulado
        query = f"{t} trailer español latino"
        yt_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
        st.video(requests.get(f"https://api.themoviedb.org/3/{'movie' if st.session_state.tipo_f == 'Película' else 'tv'}/{p['id']}/videos?api_key={TMDB_API_KEY}").json()['results'][0]['key'] if requests.get(f"https://api.themoviedb.org/3/{'movie' if st.session_state.tipo_f == 'Película' else 'tv'}/{p['id']}/videos?api_key={TMDB_API_KEY}").json()['results'] else yt_url)
        
        st.link_button("📺 Buscar Trailer en YouTube", yt_url)
        
        if st.button("⬅️ Volver"):
            del st.session_state.final
            st.rerun()

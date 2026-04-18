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

# IDs de Géneros TMDB
GENEROS_MOVIE = {"Acción": 28, "Comedia": 35, "Terror": 27, "Drama": 18, "Ciencia Ficción": 878, "Suspenso": 53, "Aventura": 12}
GENEROS_TV = {"Acción": 10759, "Comedia": 35, "Crimen": 80, "Drama": 18, "Sci-Fi & Fantasy": 10765, "Misterio": 9648}

# --- LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    with tab2:
        n = st.text_input("Nombre:").strip()
        p = st.text_input("PIN:", type="password")
        if st.button("Crear Perfil"):
            if n and p:
                user_ref = db.collection("usuarios").document(n)
                if not user_ref.get().exists:
                    user_ref.set({"nombre": n, "pin": hash_pin(p), "onboarding_completo": False})
                    st.success("¡Listo!")
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

# --- FUNCIÓN PARA OBTENER VISTAS (FILTRO) ---
def obtener_vistas():
    vistas = set()
    docs = db.collection("gustos").document(usuario_actual).collection("historial").stream()
    for d in docs:
        vistas.add(d.to_dict().get("id_tmdb"))
    docs_onb = db.collection("gustos").document(usuario_actual).collection("entrenamiento").stream()
    for d in docs_onb:
        vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

# --- FASE 1: ONBOARDING ---
user_data = user_ref.get().to_dict()
if not user_data.get("onboarding_completo"):
    st.title("🎯 Entrenamiento Inicial")
    if 'items_onb' not in st.session_state:
        url_p = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES"
        url_s = f"https://api.themoviedb.org/3/tv/popular?api_key={TMDB_API_KEY}&language=es-ES"
        st.session_state.items_onb = requests.get(url_p).json()['results'][:10] + requests.get(url_s).json()['results'][:10]
        st.session_state.idx = 0

    if st.session_state.idx < len(st.session_state.items_onb):
        item = st.session_state.items_onb[st.session_state.idx]
        tipo = "Película" if item.get('title') else "Serie"
        titulo = item.get('title') or item.get('name')
        
        st.write(f"Votando {st.session_state.idx + 1}/20")
        col1, col2 = st.columns([1, 2])
        with col1: st.image(f"https://image.tmdb.org/t/p/w500{item['poster_path']}")
        with col2:
            st.subheader(f"{titulo} ({tipo})")
            # CAMBIO: Al votar avanza solo
            rating = st.feedback("stars", key=f"onb_{item['id']}")
            if rating is not None:
                db.collection("gustos").document(usuario_actual).collection("entrenamiento").add({
                    "id_tmdb": item['id'], "stars": rating + 1, "tipo": tipo, "titulo": titulo
                })
                st.session_state.idx += 1
                st.rerun()
            if st.button("No la vi"):
                st.session_state.idx += 1
                st.rerun()
    else:
        user_ref.update({"onboarding_completo": True})
        st.rerun()
    st.stop()

# --- FASE 2: USO DIARIO ---
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.title("🚀 Buscador Inteligente")
tipo = st.radio("¿Qué querés ver?", ["Película", "Serie"], horizontal=True)
gens = GENEROS_MOVIE if tipo == "Película" else GENEROS_TV
seleccion_gens = st.multiselect("Elegí uno o varios géneros:", list(gens.keys()))

if st.button(f"Buscar {tipo}s"):
    ids = ",".join([str(gens[g]) for g in seleccion_gens])
    path = "movie" if tipo == "Película" else "tv"
    url = f"https://api.themoviedb.org/3/discover/{path}?api_key={TMDB_API_KEY}&language=es-ES&with_genres={ids}&sort_by=popularity.desc"
    
    # Filtrar las que ya vio
    ya_vistas = obtener_vistas()
    candidatas = requests.get(url).json()['results']
    st.session_state.resultados = [c for c in candidatas if c['id'] not in ya_vistas][:6]
    if not st.session_state.resultados:
        st.warning("No encontré nada nuevo en esos géneros. ¡Probá combinando otros!")

if 'resultados' in st.session_state:
    st.write("### Opciones nuevas para vos:")
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.resultados[:3]):
        titulo = p.get('title') or p.get('name')
        with cols[i]:
            st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
            if st.button(f"Ver: {titulo[:15]}...", key=f"res_{p['id']}"):
                st.session_state.final = p
                st.session_state.tipo_f = tipo
                st.rerun()

if 'final' in st.session_state:
    p = st.session_state.final
    t = p.get('title') or p.get('name')
    st.divider()
    st.header(f"🍿 Recomendación Final: {t}")
    col1, col2 = st.columns([1, 2])
    with col1: st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
    with col2:
        st.write(p['overview'])
        st.info("Disponible en: Netflix, Disney+, Max o Stremio.")
        final_rating = st.feedback("stars", key="f_rat")
        if final_rating is not None:
            db.collection("gustos").document(usuario_actual).collection("historial").add({
                "id_tmdb": p['id'], "titulo": t, "stars": final_rating + 1, "tipo": st.session_state.tipo_f
            })
            del st.session_state.final
            del st.session_state.resultados
            st.success("¡Calificado! Volviendo al buscador...")
            st.rerun()
        if st.button("Elegir otra"):
            del st.session_state.final
            st.rerun()

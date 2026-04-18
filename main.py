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

# Géneros (TMDB usa IDs diferentes para películas y series, acá unificamos los más comunes)
GENEROS_MOVIE = {"Acción": 28, "Comedia": 35, "Terror": 27, "Drama": 18, "Ciencia Ficción": 878, "Suspenso": 53}
GENEROS_TV = {"Acción": 10759, "Comedia": 35, "Crimen": 80, "Drama": 18, "Sci-Fi & Fantasy": 10765, "Misterio": 9648}

# --- LÓGICA DE USUARIOS ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    
    with tab2:
        nuevo_nombre = st.text_input("Nombre de perfil:").strip()
        nuevo_pin = st.text_input("PIN (4 números):", type="password")
        if st.button("Crear Perfil"):
            if nuevo_nombre and nuevo_pin:
                user_ref = db.collection("usuarios").document(nuevo_nombre)
                if not user_ref.get().exists:
                    user_ref.set({"nombre": nuevo_nombre, "pin": hash_pin(nuevo_pin), "onboarding_completo": False})
                    st.success("¡Registrado! Ahora entrá.")
                else: st.error("Ese nombre ya existe.")

    with tab1:
        usuarios_ref = db.collection("usuarios").stream()
        lista_nombres = [u.id for u in usuarios_ref]
        nombre_login = st.selectbox("¿Quién sos?", [""] + lista_nombres)
        pin_login = st.text_input("PIN:", type="password", key="l_pin")
        if st.button("Acceder"):
            if nombre_login:
                user_doc = db.collection("usuarios").document(nombre_login).get()
                if user_doc.exists and user_doc.to_dict()['pin'] == hash_pin(pin_login):
                    st.session_state.usuario = nombre_login
                    st.rerun()
                else: st.error("Datos incorrectos.")
    st.stop()

# --- DATOS DEL USUARIO ---
user_ref = db.collection("usuarios").document(st.session_state.usuario)
user_data = user_ref.get().to_dict()
usuario_actual = st.session_state.usuario

# --- FASE 1: ONBOARDING MIXTO ---
if not user_data.get("onboarding_completo"):
    st.title(f"🎨 ¡Hola {usuario_actual}!")
    st.subheader("Vamos a entrenar tu perfil con Pelis y Series")
    
    if 'indice_onboarding' not in st.session_state:
        st.session_state.indice_onboarding = 0
        # Mezclamos 10 pelis y 10 series famosas para el test inicial
        url_p = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES&page=1"
        url_s = f"https://api.themoviedb.org/3/tv/popular?api_key={TMDB_API_KEY}&language=es-ES&page=1"
        pelis = requests.get(url_p).json()['results'][:10]
        series = requests.get(url_s).json()['results'][:10]
        st.session_state.lista_entrenar = pelis + series
        random.shuffle(st.session_state.lista_entrenar)

    if st.session_state.indice_onboarding < len(st.session_state.lista_entrenar):
        item = st.session_state.lista_entrenar[st.session_state.indice_onboarding]
        titulo = item.get('title') if item.get('title') else item.get('name')
        tipo = "Película" if item.get('title') else "Serie"
        
        st.write(f"Evaluando {st.session_state.indice_onboarding + 1} de 20 ({tipo})")
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(f"https://image.tmdb.org/t/p/w500{item['poster_path']}")
        with col2:
            st.subheader(titulo)
            st.write(item.get('overview', 'Sin descripción.'))
            rating = st.feedback("stars", key=f"onb_{item['id']}")
            
            if st.button("Guardar"):
                if rating is not None:
                    db.collection("gustos").document(usuario_actual).collection("entrenamiento").add({
                        "id_tmdb": item['id'], "stars": rating + 1, "tipo": tipo, "titulo": titulo
                    })
                st.session_state.indice_onboarding += 1
                st.rerun()
            if st.button("No la conozco"):
                st.session_state.indice_onboarding += 1
                st.rerun()
    else:
        user_ref.update({"onboarding_completo": True})
        st.success("¡Perfil entrenado!")
        st.button("Comenzar")
    st.stop()

# --- FASE 2: USO DIARIO ---
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.title("¿Qué buscamos hoy?")
tipo_hoy = st.radio("Elegí formato:", ["Película", "Serie"], horizontal=True)

if tipo_hoy == "Película":
    genero_hoy = st.selectbox("Género:", list(GENEROS_MOVIE.keys()))
    id_gen = GENEROS_MOVIE[genero_hoy]
    endpoint = "movie"
else:
    genero_hoy = st.selectbox("Género:", list(GENEROS_TV.keys()))
    id_gen = GENEROS_TV[genero_hoy]
    endpoint = "tv"

if st.button(f"Buscar {tipo_hoy}s de {genero_hoy}"):
    url = f"https://api.themoviedb.org/3/discover/{endpoint}?api_key={TMDB_API_KEY}&language=es-ES&with_genres={id_gen}&sort_by=popularity.desc"
    st.session_state.refinar = requests.get(url).json()['results'][:6]

if 'refinar' in st.session_state:
    st.write("### Seleccioná una para ver detalles:")
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.refinar[:3]): # Mostramos 3
        titulo = p.get('title') if p.get('title') else p.get('name')
        with cols[i]:
            st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
            if st.button(f"Ver: {titulo[:15]}...", key=f"btn_{p['id']}"):
                st.session_state.seleccion_final = p
                st.session_state.tipo_final = tipo_hoy
                del st.session_state.refinar
                st.rerun()

if 'seleccion_final' in st.session_state:
    p = st.session_state.seleccion_final
    titulo_f = p.get('title') if p.get('title') else p.get('name')
    st.divider()
    st.header(f"🏆 Recomendación: {titulo_f}")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}")
    with col2:
        st.write(f"**Sinopsis:** {p['overview']}")
        st.info("📍 Disponible en: Netflix, Max, Disney+ o buscar en Stremio/Cuevana.")
        
        stars_final = st.feedback("stars", key="final_rating")
        if st.button("Guardar y elegir otra"):
            if stars_final is not None:
                db.collection("gustos").document(usuario_actual).collection("historial").add({
                    "titulo": titulo_f, "stars": stars_final + 1, "tipo": st.session_state.tipo_final
                })
            del st.session_state.seleccion_final
            st.rerun()

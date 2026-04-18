import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- CONEXIÓN FIREBASE ---
if not firebase_admin._apps:
    key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
    creds = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(creds)

db = firestore.client()
TMDB_API_KEY = st.secrets["tmdb_api_key"]

def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

# --- SISTEMA DE LOGIN Y REGISTRO ---
if 'usuario' not in st.session_state:
    st.title("🎬 Bienvenidos a Que Ver")
    
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    
    with tab2:
        nuevo_nombre = st.text_input("Tu nombre para el perfil:")
        nuevo_pin = st.text_input("Creá un PIN (solo números):", type="password")
        if st.button("Crear mi Perfil"):
            if nuevo_nombre and nuevo_pin:
                user_ref = db.collection("usuarios").document(nuevo_nombre)
                if not user_ref.get().exists:
                    user_ref.set({
                        "nombre": nuevo_nombre,
                        "pin": hash_pin(nuevo_pin)
                    })
                    st.success("¡Perfil creado! Ahora podés entrar.")
                else:
                    st.error("Ese nombre ya existe.")
            else:
                st.warning("Completá ambos campos.")

    with tab1:
        # Traemos la lista de nombres registrados desde Firebase
        usuarios_ref = db.collection("usuarios").stream()
        lista_nombres = [u.id for u in usuarios_ref]
        
        nombre_login = st.selectbox("¿Quién sos?", [""] + lista_nombres)
        pin_login = st.text_input("Tu PIN:", type="password", key="login_pin")
        
        if st.button("Acceder"):
            user_doc = db.collection("usuarios").document(nombre_login).get()
            if user_doc.exists:
                if user_doc.to_dict()['pin'] == hash_pin(pin_login):
                    st.session_state.usuario = nombre_login
                    st.rerun()
                else:
                    st.error("PIN incorrecto.")
    st.stop()

# --- APP UNA VEZ LOGUEADO ---
usuario_actual = st.session_state.usuario
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

# --- MOSTRAR PELÍCULA ---
if 'pelicula' not in st.session_state:
    page = random.randint(1, 15)
    url = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES&page={page}"
    data = requests.get(url).json()
    st.session_state.pelicula = random.choice(data['results'])

peli = st.session_state.pelicula
st.title(f"¿Qué vemos hoy, {usuario_actual}?")

col1, col2 = st.columns([1, 1])
with col1:
    st.image(f"https://image.tmdb.org/t/p/w500{peli['poster_path']}", use_container_width=True)

with col2:
    st.subheader(peli['title'])
    st.write(f"⭐ Rating: {peli['vote_average']}")
    
    if st.button("👍 Me gusta"):
        db.collection("gustos").document(usuario_actual).collection("likes").add({
            "peli_id": peli['id'], "titulo": peli['title']
        })
        del st.session_state.pelicula
        st.rerun()

    if st.button("✅ Ya la vi"):
        db.collection("historial").document(usuario_actual).collection("vistas").add({
            "peli_id": peli['id'], "titulo": peli['title']
        })
        del st.session_state.pelicula
        st.rerun()

    if st.button("➡️ Siguiente"):
        del st.session_state.pelicula
        st.rerun()

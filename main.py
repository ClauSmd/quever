import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- 1. CONFIGURACIÓN DE LLAVES Y CONEXIÓN ---
# Buscamos la clave de TMDB (Películas)
if "tmdb_api_key" in st.secrets:
    TMDB_API_KEY = st.secrets["tmdb_api_key"]
elif "text_secrets" in st.secrets and "tmdb_api_key" in st.secrets["text_secrets"]:
    TMDB_API_KEY = st.secrets["text_secrets"]["tmdb_api_key"]
else:
    st.error("⚠️ No se encontró 'tmdb_api_key' en los Secrets.")
    st.stop()

# Conexión a Firebase con protección de ID de Proyecto
if not firebase_admin._apps:
    try:
        key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
        creds = credentials.Certificate(key_dict)
        # Forzamos el ID del proyecto para evitar errores de validación
        firebase_admin.initialize_app(creds, {
            'projectId': key_dict.get('project_id')
        })
    except Exception as e:
        st.error(f"Error crítico de Firebase: {e}")
        st.stop()

db = firestore.client()

def hash_pin(pin):
    """Encripta el PIN para que no sea visible en la base de datos."""
    return hashlib.sha256(str(pin).encode()).hexdigest()

# --- 2. SISTEMA DE LOGIN Y REGISTRO ---
if 'usuario' not in st.session_state:
    st.title("🎬 Bienvenidos a Que Ver")
    st.markdown("Configurá tu perfil para recibir recomendaciones personalizadas.")
    
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    
    with tab2:
        st.subheader("Crear nuevo perfil")
        nuevo_nombre = st.text_input("Tu nombre (ej: Claudio, Marta, Amigo):")
        nuevo_pin = st.text_input("Creá un PIN numérico:", type="password", help="Solo números para tu acceso personal.")
        if st.button("Confirmar Registro"):
            if nuevo_nombre and nuevo_pin:
                # Limpiamos el nombre de espacios extra
                nuevo_nombre = nuevo_nombre.strip()
                user_ref = db.collection("usuarios").document(nuevo_nombre)
                if not user_ref.get().exists:
                    user_ref.set({
                        "nombre": nuevo_nombre, 
                        "pin": hash_pin(nuevo_pin)
                    })
                    st.success(f"¡Perfil de {nuevo_nombre} creado con éxito! Ya podés ir a la pestaña Entrar.")
                else:
                    st.error("Ese nombre ya está registrado.")
            else:
                st.warning("Por favor, completa nombre y PIN.")
    
    with tab1:
        st.subheader("Acceso al perfil")
        # Obtenemos la lista actualizada de usuarios
        usuarios_ref = db.collection("usuarios").stream()
        lista_nombres = [u.id for u in usuarios_ref]
        
        nombre_login = st.selectbox("Seleccioná tu nombre:", [""] + lista_nombres)
        pin_login = st.text_input("Ingresá tu PIN:", type="password", key="login_pin")
        
        if st.button("Acceder"):
            if nombre_login != "":
                user_doc = db.collection("usuarios").document(nombre_login).get()
                if user_doc.exists:
                    datos_user = user_doc.to_dict()
                    if datos_user['pin'] == hash_pin(pin_login):
                        st.session_state.usuario = nombre_login
                        st.rerun()
                    else:
                        st.error("PIN incorrecto. Intentá de nuevo.")
                else:
                    st.error("El usuario ya no existe.")
            else:
                st.warning("Elegí un nombre de la lista.")
    st.stop()

# --- 3. APP PRINCIPAL (SOLO SI ESTÁ LOGUEADO) ---
usuario_actual = st.session_state.usuario
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    # También limpiamos la película actual para que el siguiente usuario vea otra
    if 'pelicula' in st.session_state:
        del st.session_state.pelicula
    st.rerun()

# --- LÓGICA DE PELÍCULAS ---
if 'pelicula' not in st.session_state:
    try:
        # Buscamos una página aleatoria de pelis populares
        page = random.randint(1, 20)
        url = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES&page={page}"
        data = requests.get(url).json()
        if 'results' in data and len(data['results']) > 0:
            st.session_state.pelicula = random.choice(data['results'])
        else:
            st.error("No se pudieron cargar películas de TMDB.")
    except Exception as e:
        st.error(f"Error de conexión con TMDB: {e}")

if 'pelicula' in st.session_state:
    peli = st.session_state.pelicula
    st.title(f"¿Qué vemos hoy, {usuario_actual}?")

    col1, col2 = st.columns([1, 1.2])
    
    with col1:
        poster_path = peli.get('poster_path')
        if poster_path:
            st.image(f"https://image.tmdb.org/t/p/w500{poster_path}", use_container_width=True)
        else:
            st.write("🎬 (Sin poster disponible)")

    with col2:
        st.subheader(peli.get('title', 'Sin título'))
        st.write(f"📅 Fecha: {peli.get('release_date', 'N/A')}")
        st.write(f"⭐ Rating: {peli.get('vote_average', '0')}")
        st.write("**Resumen:**")
        resumen = peli.get('overview', 'No hay descripción disponible.')
        st.write(resumen if resumen else "No hay descripción disponible.")
        
        st.divider()
        
        # BOTONES DE INTERACCIÓN
        c1, c2, c3 = st.columns(3)
        
        with c1:
            if st.button("👍 Me gusta"):
                db.collection("gustos").document(usuario_actual).collection("likes").add({
                    "peli_id": peli['id'], "titulo": peli['title'], "fecha": firestore.SERVER_TIMESTAMP
                })
                del st.session_state.pelicula
                st.rerun()

        with c2:
            if st.button("✅ Ya la vi"):
                db.collection("historial").document(usuario_actual).collection("vistas").add({
                    "peli_id": peli['id'], "titulo": peli['title'], "fecha": firestore.SERVER_TIMESTAMP
                })
                del st.session_state.pelicula
                st.rerun()

        with c3:
            if st.button("➡️ Pasar"):
                del st.session_state.pelicula
                st.rerun()

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json

# 1. Conexión a Firebase (Solo se hace una vez)
if not firebase_admin._apps:
    # Leemos la llave desde los Secrets que pegaste
    key_dict = json.loads(st.secrets["service_account"])
    creds = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(creds)

db = firestore.client()

st.title("🎬 Que Ver")

# --- SECCIÓN: PLATAFORMAS ---
st.subheader("¿Qué servicios usas, Claudio?")
opciones = ['Netflix', 'Disney+', 'Max', 'Amazon Prime', 'Paramount+', 'Stremio']
seleccionadas = st.multiselect("Seleccioná una o varias:", opciones)

if st.button('Guardar en mi Perfil'):
    if seleccionadas:
        with st.spinner('Guardando en la nube...'):
            # Guardamos en la colección "usuarios" de tu Firebase
            doc_ref = db.collection("usuarios").document("claudio_config")
            doc_ref.set({
                "plataformas": seleccionadas,
                "ultima_actualizacion": firestore.SERVER_TIMESTAMP
            })
        st.success(f"¡Listo! Firebase ya sabe que usas: {', '.join(seleccionadas)}")
        st.balloons()
    else:
        st.warning("Elegí al menos una.")

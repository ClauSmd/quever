import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json

# Conexión Segura a Firebase
if not firebase_admin._apps:
    try:
        # CAMBIO AQUÍ: Ahora coincide con el nombre que pusiste en Secrets
        key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
        creds = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(creds)
    except Exception as e:
        st.error(f"Error de conexión: {e}")

db = firestore.client()

st.title("🎬 Que Ver")
st.subheader("Configurá tus plataformas")

# El multiselect para elegir varias
opciones = ['Netflix', 'Disney+', 'Max', 'Amazon Prime', 'Paramount+', 'Stremio']
seleccionadas = st.multiselect("Seleccioná las que usás:", opciones)

if st.button('Guardar Perfil'):
    if seleccionadas:
        with st.spinner('Sincronizando con la nube...'):
            # Guardamos en Firebase
            doc_ref = db.collection("usuarios").document("claudio_config")
            doc_ref.set({
                "plataformas": seleccionadas,
                "usuario": "Claudio"
            })
        st.success("¡Perfil actualizado con éxito!")
        st.balloons()
    else:
        st.warning("Elegí al menos una plataforma.")

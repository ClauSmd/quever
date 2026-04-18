import streamlit as st
import time

# Configuración de la página (esto la hace ver bien en celular y PC)
st.set_page_config(page_title="Que Ver - Recomendador", page_icon="🎬", layout="centered")

# Estilo personalizado (CSS) para que se vea más moderno
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 20px; height: 3em; background-color: #ff4b4b; color: white; }
    </style>
    """, unsafe_allow_html=True)

st.title("🎬 Que Ver")
st.write("Configura tu perfil para obtener las mejores recomendaciones.")

# --- SECCIÓN 1: PLATAFORMAS ---
st.subheader("¿Qué plataformas usas en Argentina?")
plataformas = st.multiselect(
    "Puedes elegir varias:",
    ['Netflix', 'Disney+', 'Max', 'Amazon Prime', 'Paramount+', 'Apple TV', 'Stremio / Otros'],
    placeholder="Seleccioná tus servicios..."
)

# --- SECCIÓN 2: EL BOTÓN CON ANIMACIÓN ---
if st.button('Guardar y continuar'):
    if not plataformas:
        st.warning("Por favor, seleccioná al menos una plataforma.")
    else:
        # Aquí activamos la animación que pediste
        with st.spinner('Optimizando tu algoritmo de recomendaciones...'):
            time.sleep(2) # Simulamos un proceso de carga con animación
        
        st.success(f"¡Genial! Guardamos: {', '.join(plataformas)}")
        st.balloons() # Una pequeña animación de festejo al terminar

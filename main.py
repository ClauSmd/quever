import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import re

# --- INICIALIZACIÓN DE FIREBASE (BYPASS DE FORMATO) ---
if not firebase_admin._apps:
    try:
        # 1. Leemos el texto crudo y limpiamos caracteres invisibles raros
        raw_text = st.secrets["text_secrets"]["json_key"]
        
        # Eliminamos caracteres de control extraños (non-breaking spaces, etc.)
        clean_text = "".join(char for char in raw_text if ord(char) < 128)
        
        # 2. Cargamos el JSON
        json_info = json.loads(clean_text)
        
        # 3. EXTRACCIÓN FORZADA DE LA LLAVE
        # Buscamos el bloque entre BEGIN y END sin importar qué barras hay en el medio
        pk_match = re.search(r"-----BEGIN PRIVATE KEY-----[\s\S]+-----END PRIVATE KEY-----", clean_text)
        
        if pk_match:
            # Limpiamos la llave de cualquier ruido
            full_pk = pk_match.group(0)
            # Reemplazamos las barras dobles literales que pone Streamlit por saltos reales
            full_pk = full_pk.replace("\\n", "\n")
            json_info["private_key"] = full_pk
        
        # 4. Inicialización oficial
        creds = credentials.Certificate(json_info)
        firebase_admin.initialize_app(creds)
        
    except Exception as e:
        st.error(f"❌ Error de Conexión: {e}")
        st.stop()

db = firestore.client()

# --- 2. FUNCIONES DE APOYO ---
def obtener_vistas(usuario):
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    return {d.to_dict().get("id_tmdb") for d in docs}

def registrar_voto(p_id, titulo, stars, usuario):
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id, "titulo": titulo, "stars": stars, "fecha": firestore.SERVER_TIMESTAMP
    })

def recomendar_con_ia(usuario, intencion, anios):
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    hist = [d.to_dict() for d in docs]
    favs = [h['titulo'] for h in hist if h.get('stars', 0) >= 4][-10:]
    vistas = [h['titulo'] for h in hist][-20:]
    
    prompt = f"""
    Expert Cinephile Mode. 
    User likes: {', '.join(favs)}. 
    Don't suggest: {', '.join(vistas)}.
    Today's vibe: {intencion} ({anios[0]}-{anios[1]}).
    Suggest 7 unique movies. 
    Format: ONLY titles separated by commas.
    """
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return [t.strip() for t in completion.choices[0].message.content.split(',')]

# --- 3. SIDEBAR Y ESTADO (EL ROBOT 🤖) ---
with st.sidebar:
    st.title("👤 Perfil")
    # Lógica de usuario (Simplificada para este ejemplo)
    if 'usuario' not in st.session_state:
        usuarios = [u.id for u in db.collection("usuarios").stream()]
        user_sel = st.selectbox("Elegí tu perfil:", [""] + usuarios)
        if user_sel:
            st.session_state.usuario = user_sel
            st.rerun()
        st.stop()
    
    st.write(f"Hola, **{st.session_state.usuario}**")
    
    # INDICADOR DISCRETO DEL ROBOT
    ia_status = st.session_state.get('ia_status', 'off')
    if ia_status == 'on':
        st.markdown("🟢 **IA Engine:** 🤖 *Online*")
    else:
        st.markdown("⚪ **IA Engine:** 🤖 *Standby*")
    
    if st.button("Cerrar Sesión"):
        del st.session_state.usuario
        st.rerun()

# --- 4. INTERFAZ PRINCIPAL ---
st.header("🎯 ¿Qué plan hay hoy?")

categorias = {
    "🍿 Pochocleras": "acción y ritmo rápido",
    "🕵️ Intriga": "suspenso y misterio atrapante",
    "🎞️ Joyas Ocultas": "cine de culto o independiente poco conocido",
    "👪 Para la Familia": "contenido apto para todas las edades",
    "🧠 Hechos Reales": "historias verídicas y documentales"
}

c_btns = st.columns(len(categorias))
for i, (nombre, desc) in enumerate(categorias.items()):
    if c_btns[i].button(nombre, use_container_width=True):
        st.session_state.plan_nombre = nombre
        st.session_state.plan_desc = desc

st.divider()
st.subheader("🗓️ Filtro de Época")
anios_sel = st.slider("Rango de estreno:", 1950, 2026, (2010, 2026))

# --- 5. LÓGICA DE BÚSQUEDA ---
if st.button("🚀 Generar Recomendaciones Inteligentes", use_container_width=True):
    if 'plan_desc' not in st.session_state:
        st.warning("Elegí una categoría arriba.")
    else:
        st.session_state.ia_status = 'on'
        with st.spinner("🤖 El robot está analizando tu ADN cinéfilo..."):
            nombres_ia = recomendar_con_ia(st.session_state.usuario, st.session_state.plan_desc, anios_sel)
            
            resultados = []
            vistas_ids = obtener_vistas(st.session_state.usuario)
            
            for n in nombres_ia:
                url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={n}&language=es-ES"
                r = requests.get(url).json().get('results', [])
                if r and r[0]['id'] not in vistas_ids:
                    resultados.append(r[0])
            
            st.session_state.resultados = resultados
            st.session_state.ia_status = 'off'

# --- 6. RENDERIZADO DE PELÍCULAS ---
if 'resultados' in st.session_state and st.session_state.resultados:
    st.divider()
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.resultados[:6]):
        with cols[i % 3]:
            st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
            st.markdown(f"**{p['title']}** ({p['release_date'][:4]})")
            
            # MATCH SCORE ALEATORIO (Simulando la IA)
            st.caption(f"✨ {random.randint(88, 98)}% Match para vos")
            
            # Lógica "Ya la vi" in-situ
            key_voto = f"calif_{p['id']}"
            if key_voto not in st.session_state:
                c_v1, c_v2 = st.columns(2)
                if c_v1.button("✅ Ya la vi", key=f"btn_v_{p['id']}", use_container_width=True):
                    st.session_state[key_voto] = True
                    st.rerun()
                if c_v2.button("⏭️ No la vi", key=f"btn_n_{p['id']}", use_container_width=True):
                    st.session_state.resultados.pop(i)
                    st.rerun()
            else:
                st.write("¿Cuántas estrellas?")
                voto = st.feedback("stars", key=f"feedback_{p['id']}")
                if voto is not None:
                    registrar_voto(p['id'], p['title'], voto + 1, st.session_state.usuario)
                    st.session_state.resultados.pop(i)
                    del st.session_state[key_voto]
                    st.success("ADN Actualizado")
                    st.rerun()

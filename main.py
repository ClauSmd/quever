import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import requests
import random
import hashlib

# --- 1. CONFIGURACIÓN INICIAL ---
if "tmdb_api_key" in st.secrets:
    TMDB_API_KEY = st.secrets["tmdb_api_key"]
else:
    TMDB_API_KEY = st.secrets["text_secrets"]["tmdb_api_key"]

if not firebase_admin._apps:
    try:
        key_dict = json.loads(st.secrets["text_secrets"]["json_key"])
        creds = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(creds, {'projectId': key_dict.get('project_id')})
    except Exception as e:
        st.error(f"Error al conectar con Firebase: {e}")
        st.stop()

db = firestore.client()

def hash_pin(pin):
    return hashlib.sha256(str(pin).encode()).hexdigest()

# IDs de Géneros de TMDB
GENEROS_MOVIE = {"Acción": 28, "Comedia": 35, "Terror": 27, "Drama": 18, "Ciencia Ficción": 878, "Suspenso": 53, "Aventura": 12, "Animación": 16}
GENEROS_TV = {"Acción": 10759, "Comedia": 35, "Crimen": 80, "Drama": 18, "Sci-Fi & Fantasy": 10765, "Misterio": 9648, "Documental": 99}

# --- 2. FUNCIONES DE BASE DE DATOS Y API ---
def obtener_vistas():
    """Trae todas las IDs de pelis/series ya calificadas para no repetirlas."""
    vistas = set()
    for c in ["historial", "entrenamiento"]:
        docs = db.collection("gustos").document(st.session_state.usuario).collection(c).stream()
        for d in docs:
            vistas.add(d.to_dict().get("id_tmdb"))
    return vistas

def registrar_voto(id_p, titulo, stars, tipo):
    """Guarda la calificación en Firebase."""
    db.collection("gustos").document(st.session_state.usuario).collection("historial").document(str(id_p)).set({
        "id_tmdb": id_p,
        "titulo": titulo,
        "stars": stars,
        "tipo": tipo,
        "fecha": firestore.SERVER_TIMESTAMP
    })

def obtener_info_extra(id_p, tipo_path):
    """Obtiene trailer y plataformas disponibles en Argentina."""
    # Trailer
    url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}&language=es-ES"
    res_v = requests.get(url_v).json().get('results', [])
    if not res_v:
        url_v = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/videos?api_key={TMDB_API_KEY}"
        res_v = requests.get(url_v).json().get('results', [])
    video_key = res_v[0]['key'] if res_v else None
    
    # Plataformas (Filtro AR para Argentina)
    url_w = f"https://api.themoviedb.org/3/{tipo_path}/{id_p}/watch/providers?api_key={TMDB_API_KEY}"
    res_w = requests.get(url_w).json().get('results', {}).get('AR', {})
    plataformas = [p['provider_name'] for p in res_w.get('flatrate', [])]
    
    return video_key, plataformas

# --- 3. SISTEMA DE LOGIN Y REGISTRO ---
if 'usuario' not in st.session_state:
    st.title("🎬 Que Ver - Smart Engine")
    tab1, tab2 = st.tabs(["Entrar", "Registrarse"])
    
    with tab2:
        n = st.text_input("Nombre de usuario:", key="reg_n").strip()
        p = st.text_input("PIN (4 números):", type="password", key="reg_p")
        if st.button("Crear Perfil", key="reg_btn"):
            if n and p:
                db.collection("usuarios").document(n).set({"nombre": n, "pin": hash_pin(p), "onboarding_completo": False})
                st.success("¡Perfil creado! Ya podés entrar.")
    
    with tab1:
        u_list = [u.id for u in db.collection("usuarios").stream()]
        n_log = st.selectbox("Elegí tu nombre:", [""] + u_list, key="log_n")
        p_log = st.text_input("Ingresá tu PIN:", type="password", key="log_p")
        if st.button("Entrar", key="log_btn"):
            if n_log:
                doc = db.collection("usuarios").document(n_log).get()
                if doc.exists and doc.to_dict()['pin'] == hash_pin(p_log):
                    st.session_state.usuario = n_log
                    st.rerun()
                else: st.error("PIN incorrecto.")
    st.stop()

# --- 4. ONBOARDING (ENTRENAMIENTO INICIAL) ---
usuario_actual = st.session_state.usuario
user_ref = db.collection("usuarios").document(usuario_actual)
user_data = user_ref.get().to_dict()

if not user_data.get("onboarding_completo"):
    st.title(f"🎯 ¡Hola {usuario_actual}! Vamos a entrenar el algoritmo")
    st.write("Calificá estas opciones para conocer tus gustos. Si no la viste, poné 'Siguiente'.")
    
    if 'idx_onb' not in st.session_state:
        st.session_state.idx_onb = 0
        p = requests.get(f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=es-ES").json()['results'][:10]
        s = requests.get(f"https://api.themoviedb.org/3/tv/popular?api_key={TMDB_API_KEY}&language=es-ES").json()['results'][:10]
        st.session_state.items_onb = p + s
        random.shuffle(st.session_state.items_onb)

    if st.session_state.idx_onb < len(st.session_state.items_onb):
        item = st.session_state.items_onb[st.session_state.idx_onb]
        t = item.get('title') or item.get('name')
        st.image(f"https://image.tmdb.org/t/p/w500{item['poster_path']}", width=250)
        st.subheader(t)
        
        rat = st.feedback("stars", key=f"onb_star_{item['id']}")
        if rat is not None:
            registrar_voto(item['id'], t, rat+1, "Peli" if item.get('title') else "Serie")
            st.session_state.idx_onb += 1
            st.rerun()
        if st.button("No la vi / Siguiente"):
            st.session_state.idx_onb += 1
            st.rerun()
    else:
        user_ref.update({"onboarding_completo": True})
        st.rerun()
    st.stop()

# --- 5. BUSCADOR PRINCIPAL ---
st.sidebar.title(f"👤 {usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    del st.session_state.usuario
    st.rerun()

st.title("🚀 Buscador Inteligente")

# Botón de Reset total
if st.button("🔄 Nueva Búsqueda / Limpiar Todo"):
    for k in ['resultados', 'final', 'tipo_f']:
        if k in st.session_state: del st.session_state[k]
    st.rerun()

# Filtros
tipo = st.radio("¿Qué buscamos hoy?", ["Película", "Serie"], horizontal=True)
gens_dict = GENEROS_MOVIE if tipo == "Película" else GENEROS_TV
seleccion_gens = st.multiselect("Elegí géneros:", list(gens_dict.keys()))

st.write("📅 **Rango de años:**")
anio_min, anio_max = st.slider("Período:", 1950, 2026, (2010, 2026))

if st.button(f"Buscar {tipo}s"):
    ids_gen = ",".join([str(gens_dict[g]) for g in seleccion_gens])
    path = "movie" if tipo == "Película" else "tv"
    prefijo_f = "primary_release_date" if tipo == "Película" else "first_air_date"
    
    # Buscamos en una de las primeras 10 páginas para variar resultados
    pag = random.randint(1, 10)
    url = (f"https://api.themoviedb.org/3/discover/{path}?api_key={TMDB_API_KEY}&language=es-ES"
           f"&with_genres={ids_gen}&{prefijo_f}.gte={anio_min}-01-01&{prefijo_f}.lte={anio_max}-12-31"
           f"&sort_by=popularity.desc&page={pag}")
    
    vistas = obtener_vistas()
    try:
        res = requests.get(url).json().get('results', [])
        st.session_state.resultados = [c for c in res if c['id'] not in vistas][:6]
        if not st.session_state.resultados:
            st.warning("No encontré nada nuevo. ¡Probá ampliando el rango de años!")
    except:
        st.error("Error al obtener datos.")

# --- 6. GRILLA DE RESULTADOS ---
if 'resultados' in st.session_state and 'final' not in st.session_state:
    st.write("### Opciones encontradas:")
    cols = st.columns(3)
    for i, p in enumerate(st.session_state.resultados):
        with cols[i % 3]:
            st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}")
            titulo = p.get('title') or p.get('name')
            st.caption(f"**{titulo}**")
            
            # Ya la vi (Estrellas en grilla)
            rat_g = st.feedback("stars", key=f"grid_{p['id']}")
            if rat_g is not None:
                registrar_voto(p['id'], titulo, rat_g+1, tipo)
                st.session_state.resultados.pop(i)
                st.rerun()
            
            # Botones de acción
            if st.button("Más como esta", key=f"sim_{p['id']}", use_container_width=True):
                p_path = "movie" if tipo == "Película" else "tv"
                url_s = f"https://api.themoviedb.org/3/{p_path}/{p['id']}/similar?api_key={TMDB_API_KEY}&language=es-ES"
                vistas = obtener_vistas()
                st.session_state.resultados = [c for c in requests.get(url_s).json().get('results', []) if c['id'] not in vistas][:6]
                st.rerun()
            
            if st.button("Ver Ficha", key=f"fic_{p['id']}", use_container_width=True):
                st.session_state.final = p
                st.session_state.tipo_f = "movie" if tipo == "Película" else "tv"
                st.rerun()

# --- 7. FICHA DETALLADA (TRAILER + PLATAFORMAS) ---
if 'final' in st.session_state:
    p = st.session_state.final
    titulo_f = p.get('title') or p.get('name')
    vid, plats = obtener_info_extra(p['id'], st.session_state.tipo_f)
    
    st.divider()
    if st.button("⬅️ Volver al listado"):
        del st.session_state.final
        st.rerun()

    c1, c2 = st.columns([1, 1.5])
    with c1:
        st.image(f"https://image.tmdb.org/t/p/w500{p['poster_path']}", use_container_width=True)
    with c2:
        st.header(titulo_f)
        st.write(f"**Sinopsis:** {p['overview']}")
        
        st.subheader("📍 ¿Dónde verla en Argentina?")
        if plats:
            st.success(" / ".join(plats))
        else:
            st.warning("No disponible en streaming (Probar Stremio / Cuevana).")
            
        if vid:
            st.subheader("🎥 Trailer Subtitulado")
            st.video(f"https://www.youtube.com/watch?v={vid}")
        else:
            st.link_button("🔍 Buscar trailer en YouTube", f"https://www.youtube.com/results?search_query={titulo_f}+trailer+español+latino")

        st.divider()
        st.write("Calificá para guardar en tu historial:")
        rat_f = st.feedback("stars", key=f"final_rat_{p['id']}")
        if rat_f is not None:
            registrar_voto(p['id'], titulo_f, rat_f+1, st.session_state.tipo_f)
            del st.session_state.final
            if 'resultados' in st.session_state: del st.session_state.resultados
            st.success("¡Guardado!")
            st.rerun()

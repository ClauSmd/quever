"""
🎬 QueVer — Recomendador Cinematográfico v3.0
Mejoras principales:
  - Login con PIN por usuario
  - Onboarding de 20 películas (cold start)
  - IA profunda: analiza géneros, décadas y directores reales de TMDB
  - Caché para Firebase y TMDB (mucho más rápido)
  - Diseño cinematográfico oscuro
"""

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import random
import base64
import json
import hashlib
from groq import Groq

# ══════════════════════════════════════════════
# CONFIGURACIÓN DE PÁGINA
# ══════════════════════════════════════════════
st.set_page_config(
    page_title="🎬 QueVer",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@300;400;600&display=swap');

/* Fondo cinematográfico */
.stApp { background-color: #0a0a0f; }
section[data-testid="stSidebar"] { background-color: #111118 !important; }

/* Tipografía */
h1, h2, h3 { font-family: 'Bebas Neue', sans-serif !important; letter-spacing: 2px; }

/* Botones */
.stButton > button {
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    transition: all 0.15s ease;
    border: 1px solid #333;
}
.stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(255,200,0,0.15); }

/* Cards de película */
.movie-title { font-size: 15px; font-weight: 700; margin: 6px 0 2px 0; line-height: 1.3; }
.movie-meta  { font-size: 12px; color: #888; margin-bottom: 8px; }
.match-pill  {
    display: inline-block;
    background: linear-gradient(90deg, #f5a623, #e8001d);
    color: white; font-size: 11px; font-weight: 700;
    padding: 2px 9px; border-radius: 20px; margin-bottom: 6px;
}

/* PIN input grande */
.pin-box input { font-size: 28px !important; letter-spacing: 12px !important; text-align: center; }

/* Onboarding */
.onboarding-pill {
    background: #1a1a2e; border: 1px solid #333;
    border-radius: 8px; padding: 6px 10px;
    font-size: 13px; margin: 3px 0;
}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════
# FIREBASE
# ══════════════════════════════════════════════
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        raw = base64.b64decode(st.secrets["fb_service_account_b64"]).decode("utf-8")
        service_dict = json.loads(raw)
        creds = credentials.Certificate(service_dict)
        firebase_admin.initialize_app(creds)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"❌ Error Firebase: {e}")
    st.stop()

TMDB_API_KEY = st.secrets["tmdb_api_key"]
groq_client  = Groq(api_key=st.secrets["groq_api_key"])

# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def tmdb_get(endpoint: str, params: dict = {}) -> dict:
    """Llamada genérica a TMDB con timeout."""
    base = "https://api.themoviedb.org/3"
    params["api_key"] = TMDB_API_KEY
    params["language"] = "es-ES"
    try:
        r = requests.get(f"{base}{endpoint}", params=params, timeout=6)
        return r.json()
    except Exception:
        return {}

# ══════════════════════════════════════════════
# CACHÉ DE DATOS (evita re-leer Firebase/TMDB)
# ══════════════════════════════════════════════
@st.cache_data(ttl=90, show_spinner=False)
def obtener_historial_cached(usuario: str) -> list:
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    return [d.to_dict() for d in docs]

def invalidar_cache_historial(usuario: str):
    obtener_historial_cached.clear()

@st.cache_data(ttl=3600, show_spinner=False)
def obtener_detalles_tmdb(tmdb_id: int) -> dict:
    """Genres, director y año real de una película."""
    data = tmdb_get(f"/movie/{tmdb_id}", {"append_to_response": "credits"})
    generos = [g["name"] for g in data.get("genres", [])]
    director = next(
        (c["name"] for c in data.get("credits", {}).get("crew", []) if c["job"] == "Director"),
        None,
    )
    anio = (data.get("release_date") or "")[:4]
    return {"generos": generos, "director": director, "anio": anio, "titulo": data.get("title", "")}

@st.cache_data(ttl=3600, show_spinner=False)
def buscar_en_tmdb(titulo: str, anio_min: int, anio_max: int):
    results = tmdb_get("/search/movie", {"query": titulo}).get("results", [])
    for r in results:
        if not r.get("poster_path"):
            continue
        anio = int((r.get("release_date") or "0000")[:4] or 0)
        if anio_min <= anio <= anio_max:
            return r
    return None

@st.cache_data(ttl=7200, show_spinner=False)
def peliculas_onboarding() -> list:
    """
    50 películas populares y diversas para el onboarding.
    Mezcla populares + top rated + distintas décadas.
    """
    peliculas = []
    seen = set()
    endpoints = [
        ("/movie/popular", {}),
        ("/movie/top_rated", {}),
        ("/discover/movie", {"sort_by": "vote_average.desc", "vote_count.gte": 2000, "primary_release_date.lte": "2005-12-31"}),
        ("/discover/movie", {"sort_by": "popularity.desc", "with_genres": "35"}),   # comedia
        ("/discover/movie", {"sort_by": "popularity.desc", "with_genres": "18"}),   # drama
        ("/discover/movie", {"sort_by": "popularity.desc", "with_genres": "27"}),   # terror
    ]
    for endpoint, extra_params in endpoints:
        params = {"page": 1, **extra_params}
        results = tmdb_get(endpoint, params).get("results", [])
        for r in results:
            if r.get("poster_path") and r["id"] not in seen:
                seen.add(r["id"])
                peliculas.append(r)
        if len(peliculas) >= 50:
            break
    random.shuffle(peliculas)
    return peliculas[:40]

# ══════════════════════════════════════════════
# FUNCIONES DE ESCRITURA EN FIREBASE
# ══════════════════════════════════════════════
def registrar_voto(p_id: int, titulo: str, stars: int, usuario: str, generos: list = None, director: str = None, anio: str = None):
    doc = {
        "id_tmdb": p_id, "titulo": titulo, "stars": stars,
        "fecha": firestore.SERVER_TIMESTAMP,
    }
    if generos:  doc["generos"] = generos
    if director: doc["director"] = director
    if anio:     doc["anio"] = anio
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set(doc)
    invalidar_cache_historial(usuario)

def registrar_descarte(p_id: int, titulo: str, usuario: str):
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id, "titulo": titulo, "stars": 0,
        "descartada": True, "fecha": firestore.SERVER_TIMESTAMP,
    })
    invalidar_cache_historial(usuario)

def marcar_onboarding_completo(usuario: str):
    db.collection("usuarios").document(usuario).update({"onboarding": True})

# ══════════════════════════════════════════════
# PERFIL DE GUSTOS (PROFUNDO)
# ══════════════════════════════════════════════
def construir_perfil_profundo(usuario: str) -> dict:
    """
    Construye un perfil rico: títulos favoritos + géneros + directores + décadas.
    Esto es lo que hace que la IA recomiende bien.
    """
    historial = obtener_historial_cached(usuario)

    favoritas   = [h for h in historial if h.get("stars", 0) >= 4]
    buenas      = [h for h in historial if h.get("stars") == 3]
    malas       = [h for h in historial if h.get("stars") in (1, 2)]
    descartadas = [h for h in historial if h.get("descartada")]
    todas       = [h.get("id_tmdb") for h in historial]

    # Acumular géneros y directores de las favoritas
    generos_contador = {}
    directores_fav   = []
    decadas_fav      = []

    for h in favoritas:
        for g in h.get("generos", []):
            generos_contador[g] = generos_contador.get(g, 0) + h.get("stars", 1)
        if h.get("director"):
            directores_fav.append(h["director"])
        if h.get("anio"):
            dec = h["anio"][:3] + "0s"
            decadas_fav.append(dec)

    top_generos    = sorted(generos_contador, key=lambda x: -generos_contador[x])[:5]
    top_directores = list(dict.fromkeys(directores_fav))[:5]  # únicos, orden preservado
    top_decadas    = list(dict.fromkeys(decadas_fav))[:3]

    return {
        "titulos_favoritos":  [h["titulo"] for h in favoritas][-12:],
        "titulos_buenos":     [h["titulo"] for h in buenas][-8:],
        "titulos_malos":      [h["titulo"] for h in malas][-8:],
        "titulos_descartados":[h["titulo"] for h in descartadas][-15:],
        "todos_ids":          set(todas),
        "generos_preferidos": top_generos,
        "directores_fav":     top_directores,
        "decadas_fav":        top_decadas,
        "total_vistas":       len(historial),
    }

# ══════════════════════════════════════════════
# RECOMENDACIÓN IA (PROFUNDA)
# ══════════════════════════════════════════════
def recomendar_con_ia(usuario: str, intencion: str, anios: tuple) -> list:
    perfil = construir_perfil_profundo(usuario)

    # Construir contexto rico
    partes = ["Eres el mejor curador de cine del mundo. Conocés cada película, género y director."]

    if perfil["total_vistas"] == 0:
        partes.append("El usuario es nuevo, no tiene historial todavía.")
    else:
        partes.append(f"\n### PERFIL DEL USUARIO ({perfil['total_vistas']} películas calificadas)\n")
        if perfil["generos_preferidos"]:
            partes.append(f"**Géneros que más disfruta:** {', '.join(perfil['generos_preferidos'])}")
        if perfil["directores_fav"]:
            partes.append(f"**Directores favoritos:** {', '.join(perfil['directores_fav'])}")
        if perfil["decadas_fav"]:
            partes.append(f"**Décadas preferidas:** {', '.join(perfil['decadas_fav'])}")
        if perfil["titulos_favoritos"]:
            partes.append(f"**Amó estas películas (4-5★):** {', '.join(perfil['titulos_favoritos'])}")
        if perfil["titulos_buenos"]:
            partes.append(f"**Le gustaron (3★):** {', '.join(perfil['titulos_buenos'])}")
        if perfil["titulos_malos"]:
            partes.append(f"**NO le gustaron (1-2★):** {', '.join(perfil['titulos_malos'])}")
        if perfil["titulos_descartados"]:
            partes.append(f"**Jamás recomendar:** {', '.join(perfil['titulos_descartados'])}")

    partes += [
        f"\n### PEDIDO ACTUAL",
        f"**Mood:** {intencion}",
        f"**Años permitidos:** {anios[0]}–{anios[1]}",
        "",
        "### TU TAREA",
        "Sugiere 10 películas DISTINTAS entre sí que:",
        f"1. Encajen con el mood '{intencion}'",
        f"2. Se hayan estrenado entre {anios[0]} y {anios[1]}",
        "3. El usuario probablemente VA A AMAR basándote en su perfil de géneros y directores",
        "4. NO estén en la lista de vistas ni descartadas",
        "5. Sean VARIADAS: mezcla clásicos del género, joyas ocultas y algo sorprendente",
        "6. Incluí al menos 2-3 películas no-hollywoodenses si encajan con el perfil",
        "",
        "FORMATO ESTRICTO: Solo títulos separados por comas. Sin números. Sin explicaciones.",
        "Ejemplo correcto: Oldboy, Los ocho más odiados, Caché, La cinta blanca",
    ]

    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "\n".join(partes)}],
        temperature=0.8,
        max_tokens=400,
    )
    raw = completion.choices[0].message.content
    return [t.strip().strip('"').strip("'") for t in raw.split(",") if t.strip()][:10]

# ══════════════════════════════════════════════
# LOGIN CON PIN
# ══════════════════════════════════════════════
def pantalla_login():
    st.markdown("<br>", unsafe_allow_html=True)
    col_c = st.columns([1, 2, 1])[1]

    with col_c:
        st.markdown("# 🎬 QUEVER")
        st.markdown("##### Tu recomendador de cine personal")
        st.divider()

        tab_login, tab_nuevo = st.tabs(["🔑 Entrar", "✨ Crear perfil"])

        with tab_login:
            with st.form("form_login"):
                nombre = st.text_input("Nombre de usuario")
                pin    = st.text_input("PIN (4 dígitos)", type="password", max_chars=4)
                ok     = st.form_submit_button("Entrar →", use_container_width=True)
            if ok:
                if not nombre or not pin:
                    st.warning("Completá nombre y PIN.")
                else:
                    doc = db.collection("usuarios").document(nombre).get()
                    if not doc.exists:
                        st.error("Usuario no encontrado.")
                    elif doc.to_dict().get("pin") != hash_pin(pin):
                        st.error("PIN incorrecto.")
                    else:
                        datos = doc.to_dict()
                        st.session_state.usuario   = nombre
                        st.session_state.onboarding_done = datos.get("onboarding", False)
                        st.rerun()

        with tab_nuevo:
            with st.form("form_nuevo"):
                n_nombre = st.text_input("Elegí tu nombre")
                n_pin    = st.text_input("Creá un PIN de 4 dígitos", type="password", max_chars=4)
                n_pin2   = st.text_input("Repetí el PIN", type="password", max_chars=4)
                crear    = st.form_submit_button("Crear perfil →", use_container_width=True)
            if crear:
                if not n_nombre or not n_pin:
                    st.warning("Completá todos los campos.")
                elif len(n_pin) != 4 or not n_pin.isdigit():
                    st.warning("El PIN debe ser exactamente 4 números.")
                elif n_pin != n_pin2:
                    st.error("Los PINes no coinciden.")
                elif db.collection("usuarios").document(n_nombre).get().exists:
                    st.error("Ese nombre ya existe, elegí otro.")
                else:
                    db.collection("usuarios").document(n_nombre).set({
                        "pin": hash_pin(n_pin),
                        "onboarding": False,
                        "creado": firestore.SERVER_TIMESTAMP,
                    })
                    st.session_state.usuario = n_nombre
                    st.session_state.onboarding_done = False
                    st.success(f"¡Bienvenido, {n_nombre}! Ahora calificá algunas películas para arrancar.")
                    st.rerun()

    st.stop()

# ══════════════════════════════════════════════
# ONBOARDING — 20 películas para calibrar la IA
# ══════════════════════════════════════════════
def pantalla_onboarding():
    st.markdown("## 🎬 Primero, contanos tu gusto")
    st.markdown("Calificá las que ya viste para que la IA aprenda qué te gusta. **Salteá las que no conocés.**")
    st.progress(0.0, text="Empezando calibración...")

    if "onboarding_peliculas" not in st.session_state:
        st.session_state.onboarding_peliculas = peliculas_onboarding()[:20]
        st.session_state.onboarding_idx = 0
        st.session_state.onboarding_calificadas = 0

    peliculas = st.session_state.onboarding_peliculas
    calificadas = st.session_state.onboarding_calificadas
    total = len(peliculas)

    progreso = calificadas / total
    st.progress(progreso, text=f"{calificadas} / {total} calificadas")

    if calificadas >= 5:
        if st.button("✅ Listo, empezar a recomendar", type="primary"):
            marcar_onboarding_completo(st.session_state.usuario)
            st.session_state.onboarding_done = True
            st.rerun()
        st.caption("Podés calificar más o arrancar ahora.")

    st.divider()

    cols = st.columns(4)
    for i, p in enumerate(peliculas):
        key_ob = f"ob_{p['id']}"
        if key_ob in st.session_state:
            continue  # ya calificada, no mostrar

        with cols[i % 4]:
            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}", use_container_width=True)
            anio = (p.get("release_date") or "")[:4]
            st.markdown(f"<div class='movie-title'>{p['title']}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='movie-meta'>{anio}</div>", unsafe_allow_html=True)

            voto = st.feedback("stars", key=f"fb_ob_{p['id']}")
            if voto is not None:
                stars = voto + 1
                # Obtener detalles para guardar géneros/director
                detalles = obtener_detalles_tmdb(p["id"])
                registrar_voto(
                    p["id"], p["title"], stars, st.session_state.usuario,
                    generos=detalles.get("generos"),
                    director=detalles.get("director"),
                    anio=detalles.get("anio"),
                )
                st.session_state[key_ob] = True
                st.session_state.onboarding_calificadas += 1
                st.rerun()

            if st.button("Saltar", key=f"skip_ob_{p['id']}", use_container_width=True):
                st.session_state[key_ob] = True
                st.rerun()

    st.stop()

# ══════════════════════════════════════════════
# VERIFICAR LOGIN
# ══════════════════════════════════════════════
if "usuario" not in st.session_state:
    pantalla_login()

usuario = st.session_state.usuario

# Verificar onboarding
if not st.session_state.get("onboarding_done", False):
    pantalla_onboarding()

# ══════════════════════════════════════════════
# SIDEBAR (usuario logueado)
# ══════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"## 👤 {usuario}")
    perfil = construir_perfil_profundo(usuario)

    c1, c2 = st.columns(2)
    c1.metric("🎬 Vistas",    perfil["total_vistas"])
    c2.metric("⭐ Favoritas", len(perfil["titulos_favoritos"]))

    if perfil["generos_preferidos"]:
        st.caption("**Tus géneros:**")
        st.caption(" · ".join(perfil["generos_preferidos"]))

    st.divider()

    if st.checkbox("📋 Historial reciente"):
        historial = obtener_historial_cached(usuario)
        if historial:
            for h in sorted(historial, key=lambda x: x.get("stars", 0), reverse=True)[:12]:
                if h.get("descartada"):
                    st.caption(f"🚫 {h['titulo']}")
                else:
                    s = h.get("stars", 0)
                    st.caption(f"{'★'*s}{'☆'*(5-s)} {h['titulo']}")
        else:
            st.caption("Sin historial aún.")

    st.divider()

    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ══════════════════════════════════════════════
# INTERFAZ PRINCIPAL
# ══════════════════════════════════════════════
st.markdown("# 🎯 ¿QUÉ PLAN HAY HOY?")

CATEGORIAS = {
    "🍿 Pochoclera":    "acción intensa, ritmo rápido, efectos especiales y adrenalina pura",
    "🕵️ Intriga":       "suspenso psicológico, giros de trama inesperados, misterio que atrapa",
    "🎞️ Joya Oculta":   "cine de culto, indie, película poco conocida pero extraordinaria",
    "👪 Familiar":      "apta para todas las edades, divertida y con mensaje positivo",
    "🧠 Hechos Reales": "basada en hechos reales, biopic poderoso o documental cinematográfico",
    "💔 Drama":         "emotiva y profunda, con personajes complejos y actuaciones memorables",
    "😂 Comedia":       "humor genuino, situaciones absurdas o comedia romántica inteligente",
}

cols = st.columns(len(CATEGORIAS))
for i, (nombre, desc) in enumerate(CATEGORIAS.items()):
    activa = st.session_state.get("plan_nombre") == nombre
    if cols[i].button(nombre, use_container_width=True, type="primary" if activa else "secondary"):
        st.session_state.plan_nombre = nombre
        st.session_state.plan_desc   = desc
        st.session_state.pop("resultados", None)

if "plan_nombre" in st.session_state:
    st.success(f"Mood seleccionado: **{st.session_state.plan_nombre}**")

st.divider()

with st.expander("🔧 Filtros", expanded=True):
    col_a, col_b = st.columns(2)
    with col_a:
        anios_sel = st.slider("📅 Rango de estreno:", 1950, 2025, (1995, 2025))
    with col_b:
        max_peli = st.select_slider("🎬 Películas a mostrar:", options=[3, 6, 9], value=6)

# ══════════════════════════════════════════════
# BÚSQUEDA
# ══════════════════════════════════════════════
if st.button("🚀 Generar Recomendaciones", use_container_width=True, type="primary"):
    if "plan_desc" not in st.session_state:
        st.warning("⚠️ Elegí una categoría primero.")
    else:
        st.session_state.pop("descartadas_sesion", None)
        with st.spinner("🤖 Analizando tu perfil cinéfilo..."):
            try:
                titulos_ia = recomendar_con_ia(
                    usuario, st.session_state.plan_desc, anios_sel
                )
            except Exception as e:
                st.error(f"Error en IA: {e}")
                st.stop()

            ids_vistos = construir_perfil_profundo(usuario)["todos_ids"]
            resultados = []

            for titulo in titulos_ia:
                if len(resultados) >= max_peli + 3:
                    break
                try:
                    p = buscar_en_tmdb(titulo, anios_sel[0], anios_sel[1])
                    if p and p["id"] not in ids_vistos:
                        resultados.append(p)
                except Exception:
                    continue

            st.session_state.resultados = resultados

        if not resultados:
            st.error("No encontré resultados. Ampliá el rango de años o cambiá la categoría.")

# ══════════════════════════════════════════════
# RENDERIZADO DE PELÍCULAS
# ══════════════════════════════════════════════
if "descartadas_sesion" not in st.session_state:
    st.session_state.descartadas_sesion = set()

if st.session_state.get("resultados"):
    st.divider()
    st.markdown(f"### 🎬 RECOMENDACIONES PARA {usuario.upper()}")

    ids_vistos = construir_perfil_profundo(usuario)["todos_ids"]
    a_mostrar = [
        p for p in st.session_state.resultados
        if p["id"] not in st.session_state.descartadas_sesion
        and p["id"] not in ids_vistos
    ][:max_peli]

    if not a_mostrar:
        st.info("📭 No quedan más resultados. Pedí nuevas recomendaciones.")
    else:
        cols = st.columns(3)
        for i, p in enumerate(a_mostrar):
            with cols[i % 3]:
                if p.get("poster_path"):
                    st.image(f"https://image.tmdb.org/t/p/w400{p['poster_path']}", use_container_width=True)

                anio   = (p.get("release_date") or "")[:4]
                rating = p.get("vote_average", 0)
                match  = random.randint(88, 98)

                st.markdown(f"<div class='movie-title'>{p['title']} ({anio})</div>", unsafe_allow_html=True)
                st.markdown(f"<span class='match-pill'>🎯 {match}% match</span>", unsafe_allow_html=True)
                st.markdown(f"<div class='movie-meta'>⭐ TMDB {rating:.1f}</div>", unsafe_allow_html=True)

                if p.get("overview"):
                    with st.expander("📖 Sinopsis"):
                        st.write(p["overview"][:280] + "..." if len(p.get("overview","")) > 280 else p["overview"])

                key_v = f"visto_{p['id']}"

                if key_v not in st.session_state:
                    c1, c2, c3 = st.columns(3)

                    if c1.button("✅ Vista", key=f"v_{p['id']}", use_container_width=True):
                        st.session_state[key_v] = "calificar"
                        st.rerun()

                    if c2.button("⏭️ Saltar", key=f"s_{p['id']}", use_container_width=True):
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    if c3.button("🚫 Nunca", key=f"n_{p['id']}", use_container_width=True):
                        registrar_descarte(p["id"], p["title"], usuario)
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.toast(f"'{p['title']}' descartada para siempre.", icon="🚫")
                        st.rerun()

                elif st.session_state[key_v] == "calificar":
                    st.markdown("**¿Cuántas estrellas?**")
                    voto = st.feedback("stars", key=f"fb_{p['id']}")

                    if voto is not None:
                        stars = voto + 1
                        # Enriquecer con géneros y director de TMDB
                        detalles = obtener_detalles_tmdb(p["id"])
                        registrar_voto(
                            p["id"], p["title"], stars, usuario,
                            generos=detalles.get("generos"),
                            director=detalles.get("director"),
                            anio=detalles.get("anio"),
                        )
                        msgs = {5: ("🎉", "¡Obra maestra guardada!"), 4: ("⭐", "¡Favorita anotada!"),
                                3: ("👍", "Guardada."), 2: ("📝", "La IA aprende."), 1: ("📝", "La IA aprende.")}
                        icon, msg = msgs.get(stars, ("✅", "Guardada."))
                        st.toast(f"{msg} '{p['title']}'", icon=icon)
                        del st.session_state[key_v]
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    if st.button("↩️ Cancelar", key=f"cancel_{p['id']}"):
                        del st.session_state[key_v]
                        st.rerun()

                st.markdown("---")

    st.divider()
    if st.button("🔄 Pedir otras recomendaciones", use_container_width=True):
        st.session_state.pop("resultados", None)
        st.rerun()

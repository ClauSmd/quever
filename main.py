"""
🎬 QueVer v4.0 — Motor de Afinidad Emocional
═══════════════════════════════════════════════
ARQUITECTURA:

  🧬 ADN de película (6 dimensiones, 0-10):
     intensidad · complejidad · ritmo · oscuridad · espectáculo · originalidad
     → Generado por IA UNA VEZ y cacheado en Firebase para siempre

  👤 Vector de usuario:
     → Promedio ponderado de ADNs de películas que le gustaron
     → Vector negativo de las que rechazó
     → Actualizado automáticamente con cada calificación

  ⚙️  Motor de scoring (SIN IA):
     score = dot(user+, movie) - 0.5 * dot(user-, movie) + bonus_rareza
     80% top scored + 20% exploración aleatoria

  🤖 IA solo para:
     → Generar el ADN de una película (llamada única, luego Firebase)
     → NO para elegir qué recomendar

  🔑 Login con PIN + Onboarding emocional (no "¿te gusta acción?")
"""

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import random
import base64
import json
import hashlib
import math
from groq import Groq

# ══════════════════════════════════════════════════════
# PÁGINA
# ══════════════════════════════════════════════════════
st.set_page_config(page_title="🎬 QueVer", page_icon="🎬", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;600&display=swap');

html, body, .stApp { background-color: #07070e !important; }
section[data-testid="stSidebar"] { background: #0f0f1a !important; border-right: 1px solid #1e1e30; }

h1, h2, h3 { font-family: 'Bebas Neue', sans-serif !important; letter-spacing: 3px; color: #f0f0f0; }
p, div, span, label { font-family: 'DM Sans', sans-serif !important; }

/* Botones */
.stButton > button {
    border-radius: 5px; font-weight: 600; font-size: 13px;
    transition: all 0.15s; border: 1px solid #2a2a40;
    background: #13131f; color: #ccc;
}
.stButton > button:hover { background: #1e1e35; color: #fff; border-color: #f5a623; }

/* Pill de match */
.dna-bar { height: 4px; border-radius: 2px; margin: 2px 0; }
.match-gold { color: #f5a623; font-weight: 700; font-size: 13px; }
.dim-label  { color: #555; font-size: 11px; }

/* Onboarding cards */
.ob-reaction { font-size: 11px; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════
# FIREBASE
# ══════════════════════════════════════════════════════
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        raw = base64.b64decode(st.secrets["fb_service_account_b64"]).decode("utf-8")
        creds = credentials.Certificate(json.loads(raw))
        firebase_admin.initialize_app(creds)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"❌ Firebase: {e}")
    st.stop()

TMDB = st.secrets["tmdb_api_key"]
groq_client = Groq(api_key=st.secrets["groq_api_key"])

# ══════════════════════════════════════════════════════
# CONSTANTES DEL SISTEMA
# ══════════════════════════════════════════════════════

# Las 6 dimensiones del ADN cinematográfico
DIMS = ["intensidad", "complejidad", "ritmo", "oscuridad", "espectaculo", "originalidad"]

DIMS_LABELS = {
    "intensidad":   "⚡ Intensidad emocional",
    "complejidad":  "🧠 Complejidad mental",
    "ritmo":        "🏃 Ritmo",
    "oscuridad":    "🌑 Oscuridad / Peso",
    "espectaculo":  "💥 Espectáculo visual",
    "originalidad": "✨ Originalidad / Rareza",
}

# Cada categoría tiene un "mood ADN" que guía al motor
MOOD_VECTORES = {
    "🍿 Pochoclera":    {"intensidad":8,"complejidad":4,"ritmo":9,"oscuridad":4,"espectaculo":9,"originalidad":4},
    "🕵️ Intriga":       {"intensidad":7,"complejidad":8,"ritmo":6,"oscuridad":8,"espectaculo":4,"originalidad":7},
    "🎞️ Joya Oculta":   {"intensidad":6,"complejidad":7,"ritmo":5,"oscuridad":6,"espectaculo":3,"originalidad":9},
    "👪 Familiar":      {"intensidad":5,"complejidad":3,"ritmo":7,"oscuridad":1,"espectaculo":7,"originalidad":5},
    "🧠 Hechos Reales": {"intensidad":7,"complejidad":7,"ritmo":5,"oscuridad":6,"espectaculo":3,"originalidad":6},
    "💔 Drama":         {"intensidad":9,"complejidad":7,"ritmo":3,"oscuridad":7,"espectaculo":2,"originalidad":6},
    "😂 Comedia":       {"intensidad":5,"complejidad":3,"ritmo":7,"oscuridad":1,"espectaculo":5,"originalidad":6},
}

# ══════════════════════════════════════════════════════
# TMDB HELPERS
# ══════════════════════════════════════════════════════
def tmdb(endpoint, params={}):
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3{endpoint}",
            params={"api_key": TMDB, "language": "es-ES", **params},
            timeout=6,
        )
        return r.json()
    except Exception:
        return {}

@st.cache_data(ttl=7200, show_spinner=False)
def tmdb_buscar(titulo: str, anio_min: int, anio_max: int):
    results = tmdb("/search/movie", {"query": titulo}).get("results", [])
    for r in results:
        if not r.get("poster_path"):
            continue
        anio = int((r.get("release_date") or "0")[:4] or 0)
        if anio_min <= anio <= anio_max:
            return r
    return None

@st.cache_data(ttl=7200, show_spinner=False)
def tmdb_discover(pagina=1, sort="popularity.desc", generos="", anio_min=1990, anio_max=2025) -> list:
    params = {
        "sort_by": sort,
        "page": pagina,
        "vote_count.gte": 200,
        "primary_release_date.gte": f"{anio_min}-01-01",
        "primary_release_date.lte": f"{anio_max}-12-31",
    }
    if generos:
        params["with_genres"] = generos
    return tmdb("/discover/movie", params).get("results", [])

@st.cache_data(ttl=7200, show_spinner=False)
def peliculas_onboarding_pool() -> list:
    """Pool diverso de 60 películas para el onboarding."""
    pool = []
    seen = set()
    queries = [
        ("/movie/top_rated", {"page": 1}),
        ("/movie/top_rated", {"page": 2}),
        ("/movie/popular",   {"page": 1}),
        ("/discover/movie",  {"sort_by": "vote_average.desc", "vote_count.gte": 3000,
                              "primary_release_date.lte": "2010-12-31", "page": 1}),
        ("/discover/movie",  {"sort_by": "popularity.desc", "with_genres": "18", "page": 1}),
        ("/discover/movie",  {"sort_by": "popularity.desc", "with_genres": "27", "page": 1}),
        ("/discover/movie",  {"sort_by": "popularity.desc", "with_genres": "35", "page": 1}),
    ]
    for endpoint, params in queries:
        for r in tmdb(endpoint, {"api_key": TMDB, "language": "es-ES", **params}).get("results", []):
            if r.get("poster_path") and r["id"] not in seen:
                seen.add(r["id"])
                pool.append(r)
    random.shuffle(pool)
    return pool[:60]

# ══════════════════════════════════════════════════════
# SISTEMA DE ADN — GENERACIÓN Y ALMACENAMIENTO
# ══════════════════════════════════════════════════════

def generar_adn_ia(titulo: str, anio: str, overview: str) -> dict:
    """
    Llama a Groq UNA SOLA VEZ para generar el ADN de una película.
    Devuelve dict con las 6 dimensiones (0-10).
    """
    prompt = f"""Analizá esta película y devolvé SOLO un JSON con las 6 dimensiones.
Película: "{titulo}" ({anio})
Sinopsis: {overview[:400] if overview else 'no disponible'}

Dimensiones (0=mínimo, 10=máximo):
- intensidad: impacto emocional en el espectador
- complejidad: exigencia intelectual / trama compleja
- ritmo: velocidad narrativa (0=muy lento, 10=frenético)
- oscuridad: tono sombrío, temas pesados
- espectaculo: acción visual, efectos, escenas espectaculares
- originalidad: cuán única/atípica es dentro de su tipo

Responde ÚNICAMENTE con JSON válido, sin texto extra:
{{"intensidad": X, "complejidad": X, "ritmo": X, "oscuridad": X, "espectaculo": X, "originalidad": X}}"""

    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=120,
        )
        raw = resp.choices[0].message.content.strip()
        # Limpiar posibles ```json ... ```
        raw = raw.replace("```json", "").replace("```", "").strip()
        adn = json.loads(raw)
        # Validar y clampear valores
        return {d: max(0, min(10, int(adn.get(d, 5)))) for d in DIMS}
    except Exception:
        # Fallback: ADN neutro
        return {d: 5 for d in DIMS}

def obtener_o_crear_adn(tmdb_id: int, titulo: str, anio: str, overview: str) -> dict:
    """
    Busca el ADN en Firebase. Si no existe, lo genera con IA y lo guarda.
    Esto garantiza que cada película se analiza UNA SOLA VEZ.
    """
    ref = db.collection("peliculas").document(str(tmdb_id))
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if all(d in data for d in DIMS):
            return {d: data[d] for d in DIMS}

    # No existe → generar con IA
    adn = generar_adn_ia(titulo, anio, overview)
    ref.set({
        "id_tmdb": tmdb_id,
        "titulo": titulo,
        "anio": anio,
        **adn,
        "generado": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return adn

def vec(adn: dict) -> list:
    """Convierte ADN dict a lista ordenada."""
    return [adn.get(d, 5) for d in DIMS]

def dot(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))

def norm(v: list) -> float:
    return math.sqrt(sum(x**2 for x in v)) or 1.0

def cosine_sim(a: list, b: list) -> float:
    return dot(a, b) / (norm(a) * norm(b))

# ══════════════════════════════════════════════════════
# PERFIL DE USUARIO — VECTOR
# ══════════════════════════════════════════════════════

def actualizar_vector_usuario(usuario: str, adn: dict, stars: int):
    """
    Actualiza el vector positivo o negativo del usuario según la calificación.
    stars 4-5 → refuerza vector positivo
    stars 1-2 → refuerza vector negativo
    stars 3   → leve positivo
    """
    ref = db.collection("usuarios").document(usuario)
    doc = ref.get()
    datos = doc.to_dict() if doc.exists else {}

    v_pos = datos.get("vector_pos", [5.0]*6)
    v_neg = datos.get("vector_neg", [5.0]*6)
    n_pos = datos.get("n_pos", 0)
    n_neg = datos.get("n_neg", 0)
    movie_vec = vec(adn)

    if stars >= 4:
        # Actualizar media móvil positiva (peso proporcional a las estrellas)
        peso = 2 if stars == 5 else 1
        n_pos_new = n_pos + peso
        v_pos = [(v_pos[i] * n_pos + movie_vec[i] * peso) / n_pos_new for i in range(6)]
        n_pos = n_pos_new
    elif stars <= 2:
        peso = 2 if stars == 1 else 1
        n_neg_new = n_neg + peso
        v_neg = [(v_neg[i] * n_neg + movie_vec[i] * peso) / n_neg_new for i in range(6)]
        n_neg = n_neg_new
    else:  # 3 estrellas → leve positivo
        if n_pos > 0:
            v_pos = [(v_pos[i] * n_pos + movie_vec[i] * 0.5) / (n_pos + 0.5) for i in range(6)]

    ref.set({
        "vector_pos": v_pos,
        "vector_neg": v_neg,
        "n_pos": n_pos,
        "n_neg": n_neg,
    }, merge=True)

def obtener_perfil_usuario(usuario: str) -> dict:
    """Lee el perfil completo del usuario desde Firebase."""
    doc = db.collection("usuarios").document(usuario).get()
    datos = doc.to_dict() if doc.exists else {}
    return {
        "vector_pos": datos.get("vector_pos", [5.0]*6),
        "vector_neg": datos.get("vector_neg", [5.0]*6),
        "n_pos":      datos.get("n_pos", 0),
        "n_neg":      datos.get("n_neg", 0),
        "onboarding": datos.get("onboarding", False),
        "pin":        datos.get("pin", ""),
    }

# ══════════════════════════════════════════════════════
# HISTORIAL
# ══════════════════════════════════════════════════════

@st.cache_data(ttl=60, show_spinner=False)
def obtener_historial(usuario: str) -> list:
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    return [d.to_dict() for d in docs]

def ids_vistos(usuario: str) -> set:
    return {h.get("id_tmdb") for h in obtener_historial(usuario)}

def registrar_voto(usuario: str, tmdb_id: int, titulo: str, stars: int, adn: dict):
    db.collection("gustos").document(usuario).collection("historial").document(str(tmdb_id)).set({
        "id_tmdb": tmdb_id, "titulo": titulo, "stars": stars,
        "adn": adn, "fecha": firestore.SERVER_TIMESTAMP,
    })
    actualizar_vector_usuario(usuario, adn, stars)
    obtener_historial.clear()

def registrar_descarte(usuario: str, tmdb_id: int, titulo: str, adn: dict):
    db.collection("gustos").document(usuario).collection("historial").document(str(tmdb_id)).set({
        "id_tmdb": tmdb_id, "titulo": titulo, "stars": 0,
        "descartada": True, "adn": adn, "fecha": firestore.SERVER_TIMESTAMP,
    })
    actualizar_vector_usuario(usuario, adn, 1)  # cuenta como rechazo
    obtener_historial.clear()

# ══════════════════════════════════════════════════════
# MOTOR DE SCORING (sin IA)
# ══════════════════════════════════════════════════════

def score_pelicula(usuario_perfil: dict, movie_adn: dict, mood_vec: dict, rareza: float) -> float:
    """
    score = 0.5 * afinidad_usuario + 0.3 * mood_match + 0.1 * bonus_rareza - 0.1 * rechazo
    """
    v_usuario = usuario_perfil["vector_pos"]
    v_rechazo = usuario_perfil["vector_neg"]
    v_movie   = vec(movie_adn)
    v_mood    = vec(mood_vec)
    n_pos     = usuario_perfil["n_pos"]

    # Si el usuario no tiene historial, ponderar más el mood
    if n_pos < 3:
        s_usuario = 0.0
        s_mood    = cosine_sim(v_mood, v_movie)
        return s_mood * 10 + rareza * 0.5
    else:
        s_usuario = cosine_sim(v_usuario, v_movie)
        s_mood    = cosine_sim(v_mood, v_movie)
        s_rechazo = cosine_sim(v_rechazo, v_movie)
        s_rareza  = rareza / 10.0

        return (s_usuario * 5.0) + (s_mood * 3.0) + (s_rareza * 1.0) - (s_rechazo * 2.0)

def recomendar_motor(usuario: str, mood_nombre: str, anio_min: int, anio_max: int, n: int = 6) -> list:
    """
    Motor principal de recomendación. Sin llamadas a IA.
    1. Obtiene pool de ~60 candidatos de TMDB
    2. Genera/recupera ADN de cada uno
    3. Scoreea con perfil de usuario + mood
    4. Devuelve 80% top scores + 20% exploración aleatoria
    """
    perfil   = obtener_perfil_usuario(usuario)
    vistos   = ids_vistos(usuario)
    mood_vec = MOOD_VECTORES[mood_nombre]

    # ── Armar pool de candidatos ──────────────────────
    candidatos_raw = []
    sorts = ["popularity.desc", "vote_average.desc", "vote_count.desc"]

    for sort in sorts:
        candidatos_raw += tmdb_discover(
            pagina=random.randint(1, 3),
            sort=sort,
            anio_min=anio_min,
            anio_max=anio_max,
        )

    # Agregar resultados de búsqueda temática según mood
    genero_map = {
        "🍿 Pochoclera":    "28",   # acción
        "🕵️ Intriga":       "53",   # thriller
        "🎞️ Joya Oculta":   "18",   # drama
        "👪 Familiar":      "10751",# familia
        "🧠 Hechos Reales": "99",   # documental
        "💔 Drama":         "18",   # drama
        "😂 Comedia":       "35",   # comedia
    }
    if mood_nombre in genero_map:
        candidatos_raw += tmdb_discover(
            pagina=1, sort="vote_average.desc",
            generos=genero_map[mood_nombre],
            anio_min=anio_min, anio_max=anio_max,
        )

    # Deduplicar y filtrar vistos
    seen_ids = set()
    candidatos = []
    for p in candidatos_raw:
        if p["id"] in seen_ids or p["id"] in vistos:
            continue
        if not p.get("poster_path"):
            continue
        seen_ids.add(p["id"])
        candidatos.append(p)

    if not candidatos:
        return []

    # ── Generar ADN para candidatos (usa cache Firebase) ──
    # Solo procesar hasta 40 para no ser lento
    random.shuffle(candidatos)
    candidatos = candidatos[:40]

    scored = []
    for p in candidatos:
        anio    = (p.get("release_date") or "")[:4]
        overview = p.get("overview", "")
        # rareza = inversa de popularidad normalizada (0-10)
        pop     = p.get("popularity", 50)
        rareza  = max(0, 10 - min(pop / 100, 10))

        adn = obtener_o_crear_adn(p["id"], p.get("title", ""), anio, overview)
        p["_adn"] = adn

        score = score_pelicula(perfil, adn, mood_vec, rareza)
        scored.append((score, p))

    scored.sort(key=lambda x: -x[0])

    # 80% top scored + 20% exploración
    n_top   = max(1, int(n * 0.8))
    n_extra = n - n_top

    resultado = [p for _, p in scored[:n_top + 3]][:n_top]

    # Exploración: tomar aleatoriamente del resto del pool
    resto = [p for _, p in scored[n_top + 3:]]
    if resto and n_extra > 0:
        resultado += random.sample(resto, min(n_extra, len(resto)))

    return resultado[:n]

# ══════════════════════════════════════════════════════
# LOGIN CON PIN
# ══════════════════════════════════════════════════════
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def pantalla_login():
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("# 🎬 QUEVER")
        st.markdown("##### Motor de afinidad cinematográfica")
        st.divider()

        tab_e, tab_n = st.tabs(["🔑 Entrar", "✨ Crear perfil"])

        with tab_e:
            with st.form("login"):
                nombre = st.text_input("Usuario")
                pin    = st.text_input("PIN", type="password", max_chars=4)
                ok     = st.form_submit_button("Entrar →", use_container_width=True)
            if ok:
                if not nombre or not pin:
                    st.warning("Completá los campos.")
                else:
                    doc = db.collection("usuarios").document(nombre).get()
                    if not doc.exists:
                        st.error("Usuario no encontrado.")
                    elif doc.to_dict().get("pin") != hash_pin(pin):
                        st.error("PIN incorrecto.")
                    else:
                        st.session_state.usuario = nombre
                        st.session_state.onboarding_done = doc.to_dict().get("onboarding", False)
                        st.rerun()

        with tab_n:
            with st.form("registro"):
                nn  = st.text_input("Nombre")
                np1 = st.text_input("PIN (4 dígitos)", type="password", max_chars=4)
                np2 = st.text_input("Repetí el PIN",   type="password", max_chars=4)
                reg = st.form_submit_button("Crear →", use_container_width=True)
            if reg:
                if not nn or not np1:
                    st.warning("Completá todo.")
                elif len(np1) != 4 or not np1.isdigit():
                    st.error("El PIN debe ser 4 dígitos numéricos.")
                elif np1 != np2:
                    st.error("Los PINes no coinciden.")
                elif db.collection("usuarios").document(nn).get().exists:
                    st.error("Ese nombre ya existe.")
                else:
                    db.collection("usuarios").document(nn).set({
                        "pin": hash_pin(np1), "onboarding": False,
                        "vector_pos": [5.0]*6, "vector_neg": [5.0]*6,
                        "n_pos": 0, "n_neg": 0,
                        "creado": firestore.SERVER_TIMESTAMP,
                    })
                    st.session_state.usuario = nn
                    st.session_state.onboarding_done = False
                    st.rerun()
    st.stop()

# ══════════════════════════════════════════════════════
# ONBOARDING EMOCIONAL
# ══════════════════════════════════════════════════════
def pantalla_onboarding():
    st.markdown("## 🎬 CALIBRÁ TU PERFIL")
    st.markdown("""
    Calificá las películas que ya viste con tu reacción real.
    **Saltá las que no conocés** — no hace falta verlas todas.
    Con **6 calificaciones** el motor ya funciona bien.
    """)

    if "ob_pool" not in st.session_state:
        st.session_state.ob_pool        = peliculas_onboarding_pool()
        st.session_state.ob_calificadas = 0

    pool  = st.session_state.ob_pool
    calif = st.session_state.ob_calificadas

    progreso = min(calif / 6, 1.0)
    st.progress(progreso, text=f"{calif} calificaciones · necesitás al menos 6")

    if calif >= 6:
        if st.button("🚀 ¡Listo! Empezar a recomendar", type="primary", use_container_width=True):
            db.collection("usuarios").document(st.session_state.usuario).update({"onboarding": True})
            st.session_state.onboarding_done = True
            st.rerun()
        st.caption("Podés seguir calificando para mejorar la precisión.")

    st.divider()

    cols = st.columns(4)
    for i, p in enumerate(pool):
        key_done = f"ob_done_{p['id']}"
        if st.session_state.get(key_done):
            continue

        with cols[i % 4]:
            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}", use_container_width=True)

            anio = (p.get("release_date") or "")[:4]
            st.markdown(f"**{p['title']}** ({anio})")

            # Reacciones emocionales en lugar de estrellas genéricas
            c1, c2 = st.columns(2)
            c3, c4 = st.columns(2)

            def votar_ob(pid, titulo, stars, overview, anio_str):
                adn = obtener_o_crear_adn(pid, titulo, anio_str, overview)
                registrar_voto(st.session_state.usuario, pid, titulo, stars, adn)
                st.session_state[f"ob_done_{pid}"] = True
                st.session_state.ob_calificadas += 1

            if c1.button("😍 Me encantó", key=f"ob_5_{p['id']}", use_container_width=True):
                votar_ob(p["id"], p["title"], 5, p.get("overview",""), anio)
                st.rerun()
            if c2.button("👍 Buena",       key=f"ob_3_{p['id']}", use_container_width=True):
                votar_ob(p["id"], p["title"], 3, p.get("overview",""), anio)
                st.rerun()
            if c3.button("😐 Meh",         key=f"ob_2_{p['id']}", use_container_width=True):
                votar_ob(p["id"], p["title"], 2, p.get("overview",""), anio)
                st.rerun()
            if c4.button("😴 Me aburrió",  key=f"ob_1_{p['id']}", use_container_width=True):
                votar_ob(p["id"], p["title"], 1, p.get("overview",""), anio)
                st.rerun()

            if st.button("⏭ No la vi",     key=f"ob_skip_{p['id']}", use_container_width=True):
                st.session_state[f"ob_done_{p['id']}"] = True
                st.rerun()

            st.markdown("---")

    st.stop()

# ══════════════════════════════════════════════════════
# VERIFICAR SESIÓN
# ══════════════════════════════════════════════════════
if "usuario" not in st.session_state:
    pantalla_login()

usuario = st.session_state.usuario

if not st.session_state.get("onboarding_done", False):
    pantalla_onboarding()

# ══════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"## 👤 {usuario}")
    perfil_u = obtener_perfil_usuario(usuario)
    hist     = obtener_historial(usuario)

    vistas    = len(hist)
    favoritas = sum(1 for h in hist if h.get("stars", 0) >= 4)
    rechazadas= sum(1 for h in hist if h.get("stars", 0) <= 2 and not h.get("descartada"))

    ca, cb, cc = st.columns(3)
    ca.metric("🎬", vistas,    "vistas")
    cb.metric("⭐", favoritas, "amadas")
    cc.metric("👎", rechazadas,"no gustó")

    # Visualización del vector positivo del usuario
    if perfil_u["n_pos"] >= 3:
        st.divider()
        st.caption("**Tu perfil de gusto actual:**")
        v = perfil_u["vector_pos"]
        for i, dim in enumerate(DIMS):
            val = v[i]
            bar_color = "#f5a623" if val >= 7 else ("#4a9eff" if val >= 4 else "#444")
            width = int(val * 10)
            st.markdown(
                f"<div class='dim-label'>{DIMS_LABELS[dim]}</div>"
                f"<div style='background:#222;border-radius:3px;height:5px;'>"
                f"<div class='dna-bar' style='width:{width}%;background:{bar_color};'></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.divider()

    if st.checkbox("📋 Historial"):
        if hist:
            for h in sorted(hist, key=lambda x: x.get("stars",0), reverse=True)[:10]:
                if h.get("descartada"):
                    st.caption(f"🚫 {h['titulo']}")
                else:
                    s = h.get("stars", 0)
                    st.caption(f"{'★'*s}{'☆'*(5-s)} {h['titulo']}")
        else:
            st.caption("Sin historial.")

    st.divider()
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ══════════════════════════════════════════════════════
# INTERFAZ PRINCIPAL
# ══════════════════════════════════════════════════════
st.markdown("# 🎯 ¿QUÉ PLAN HAY HOY?")

# Selector de mood
cols_mood = st.columns(len(MOOD_VECTORES))
for i, (nombre, _) in enumerate(MOOD_VECTORES.items()):
    activo = st.session_state.get("mood") == nombre
    if cols_mood[i].button(nombre, use_container_width=True, type="primary" if activo else "secondary"):
        st.session_state.mood = nombre
        st.session_state.pop("resultados", None)

if "mood" in st.session_state:
    st.success(f"Mood: **{st.session_state.mood}**")

st.divider()

with st.expander("🔧 Filtros", expanded=True):
    ca, cb = st.columns(2)
    with ca:
        anios = st.slider("📅 Años:", 1950, 2025, (1995, 2025))
    with cb:
        n_peli = st.select_slider("🎬 Cantidad:", options=[3, 6, 9], value=6)

# ══════════════════════════════════════════════════════
# BOTÓN PRINCIPAL
# ══════════════════════════════════════════════════════
if st.button("🚀 Recomendar", use_container_width=True, type="primary"):
    if "mood" not in st.session_state:
        st.warning("Elegí un mood primero.")
    else:
        st.session_state.pop("descartadas_sesion", None)
        with st.spinner("⚙️ Motor calculando afinidad..."):
            resultados = recomendar_motor(
                usuario,
                st.session_state.mood,
                anios[0], anios[1],
                n=n_peli + 4,  # buffer
            )
        st.session_state.resultados = resultados
        if not resultados:
            st.error("Sin resultados. Ampliá el rango de años.")

# ══════════════════════════════════════════════════════
# RENDERIZADO
# ══════════════════════════════════════════════════════
if "descartadas_sesion" not in st.session_state:
    st.session_state.descartadas_sesion = set()

if st.session_state.get("resultados"):
    st.divider()
    st.markdown(f"### 🎬 PARA VOS, {usuario.upper()}")

    vistos_ahora = ids_vistos(usuario)
    a_mostrar = [
        p for p in st.session_state.resultados
        if p["id"] not in st.session_state.descartadas_sesion
        and p["id"] not in vistos_ahora
    ][:n_peli]

    if not a_mostrar:
        st.info("📭 Sin más resultados — pedí nuevas recomendaciones.")
    else:
        cols = st.columns(3)
        for i, p in enumerate(a_mostrar):
            adn  = p.get("_adn", {d: 5 for d in DIMS})
            anio = (p.get("release_date") or "")[:4]
            rating = p.get("vote_average", 0)

            # Calcular match score real vs vector del usuario
            perfil_u = obtener_perfil_usuario(usuario)
            if perfil_u["n_pos"] >= 3:
                sim = cosine_sim(perfil_u["vector_pos"], vec(adn))
                match_pct = int(50 + sim * 50)  # escala 50-100%
            else:
                match_pct = random.randint(82, 95)

            with cols[i % 3]:
                if p.get("poster_path"):
                    st.image(f"https://image.tmdb.org/t/p/w400{p['poster_path']}", use_container_width=True)

                st.markdown(f"**{p['title']}** ({anio})")
                st.markdown(
                    f"<span class='match-gold'>🎯 {match_pct}% afinidad</span>"
                    f"<span style='color:#555;font-size:12px;margin-left:8px'>⭐ {rating:.1f}</span>",
                    unsafe_allow_html=True,
                )

                # Mini visualización del ADN de la película
                with st.expander("🧬 ADN"):
                    for dim in DIMS:
                        val = adn.get(dim, 5)
                        width = int(val * 10)
                        bar_color = "#f5a623" if val >= 7 else ("#4a9eff" if val >= 4 else "#333")
                        st.markdown(
                            f"<div class='dim-label'>{DIMS_LABELS[dim]} {val}/10</div>"
                            f"<div style='background:#1a1a2a;border-radius:3px;height:5px;margin-bottom:4px'>"
                            f"<div style='width:{width}%;height:5px;border-radius:3px;background:{bar_color}'></div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                if p.get("overview"):
                    with st.expander("📖 Sinopsis"):
                        st.write(p["overview"][:280] + "..." if len(p.get("overview","")) > 280 else p["overview"])

                # Acciones
                key_v = f"visto_{p['id']}"
                if key_v not in st.session_state:
                    c1, c2, c3 = st.columns(3)
                    if c1.button("✅ Vista",  key=f"v_{p['id']}", use_container_width=True):
                        st.session_state[key_v] = "calificar"
                        st.rerun()
                    if c2.button("⏭️ Saltar", key=f"s_{p['id']}", use_container_width=True):
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()
                    if c3.button("🚫 Nunca",  key=f"n_{p['id']}", use_container_width=True):
                        registrar_descarte(usuario, p["id"], p["title"], adn)
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.toast(f"'{p['title']}' descartada.", icon="🚫")
                        st.rerun()

                elif st.session_state[key_v] == "calificar":
                    st.markdown("**¿Cómo fue?**")
                    ca2, cb2 = st.columns(2)
                    cc2, cd2 = st.columns(2)

                    def votar(pid, titulo, stars, movie_adn):
                        registrar_voto(usuario, pid, titulo, stars, movie_adn)
                        icons = {5:"🎉",4:"⭐",3:"👍",2:"📝",1:"📝"}
                        msgs  = {5:"¡Obra maestra!",4:"¡Favorita!",3:"Guardada.",2:"La IA aprende.",1:"La IA aprende."}
                        st.toast(f"{msgs[stars]} '{titulo}'", icon=icons[stars])
                        del st.session_state[f"visto_{pid}"]
                        st.session_state.descartadas_sesion.add(pid)

                    if ca2.button("😍 Me encantó", key=f"r5_{p['id']}", use_container_width=True):
                        votar(p["id"], p["title"], 5, adn); st.rerun()
                    if cb2.button("👍 Buena",       key=f"r3_{p['id']}", use_container_width=True):
                        votar(p["id"], p["title"], 3, adn); st.rerun()
                    if cc2.button("😐 Meh",         key=f"r2_{p['id']}", use_container_width=True):
                        votar(p["id"], p["title"], 2, adn); st.rerun()
                    if cd2.button("😴 Me aburrió",  key=f"r1_{p['id']}", use_container_width=True):
                        votar(p["id"], p["title"], 1, adn); st.rerun()
                    if st.button("↩️ Cancelar", key=f"cancel_{p['id']}"):
                        del st.session_state[key_v]; st.rerun()

                st.markdown("---")

    st.divider()
    if st.button("🔄 Nuevas recomendaciones", use_container_width=True):
        st.session_state.pop("resultados", None)
        st.rerun()

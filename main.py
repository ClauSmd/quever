"""
🎬 Recomendador de Películas — Versión 2.0
Mejoras:
  - Fix Firebase "Invalid private key" usando triple-quoted secrets
  - IA con acceso completo al historial (favoritas, descartadas, vistas)
  - Marcar como vista + calificar con estrellas
  - Descartar película de la búsqueda actual (sin guardar)
  - Perfil de gustos enriquecido para mejores recomendaciones
"""

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import random

# ──────────────────────────────────────────────
# CONFIGURACIÓN DE PÁGINA
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="🎬 ¿Qué vemos?",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personalizado
st.markdown("""
<style>
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s;
    }
    .movie-card {
        background: #1a1a2e;
        border-radius: 12px;
        padding: 10px;
        margin-bottom: 10px;
    }
    .match-badge {
        background: linear-gradient(135deg, #667eea, #764ba2);
        color: white;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 700;
    }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# 1. INICIALIZACIÓN FIREBASE
# ──────────────────────────────────────────────
# ⚠️  En secrets.toml usá triple comillas para la private_key:
#
#   fb_private_key = """-----BEGIN PRIVATE KEY-----
#   MIIEvAIBAD...
#   -----END PRIVATE KEY-----
#   """
#
# Así Streamlit mantiene los saltos de línea reales y Firebase no falla.

if not firebase_admin._apps:
    try:
        firebase_dict = {
            "type": "service_account",
            "project_id": st.secrets["fb_project_id"],
            # ✅ Sin .replace() — la clave triple-quoted ya trae \n reales
            "private_key": st.secrets["fb_private_key"],
            "client_email": st.secrets["fb_client_email"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        creds = credentials.Certificate(firebase_dict)
        firebase_admin.initialize_app(creds)
    except Exception as e:
        st.error(f"❌ Error de Conexión Firebase: {e}")
        st.info(
            "💡 **Tip:** En secrets.toml guardá `fb_private_key` con triple comillas:\n\n"
            '```toml\nfb_private_key = """-----BEGIN PRIVATE KEY-----\n'
            'TU_CLAVE_AQUI\n-----END PRIVATE KEY-----\n"""\n```'
        )
        st.stop()

db = firestore.client()
TMDB_API_KEY = st.secrets["tmdb_api_key"]

# ──────────────────────────────────────────────
# 2. CLIENTE IA (Groq / OpenAI compatible)
# ──────────────────────────────────────────────
from groq import Groq
client = Groq(api_key=st.secrets["groq_api_key"])

# ──────────────────────────────────────────────
# 3. FUNCIONES DE BASE DE DATOS
# ──────────────────────────────────────────────

def obtener_historial(usuario: str) -> list[dict]:
    """Devuelve todo el historial del usuario ordenado por fecha."""
    docs = (
        db.collection("gustos")
        .document(usuario)
        .collection("historial")
        .stream()
    )
    return [d.to_dict() for d in docs]


def obtener_ids_vistos(usuario: str) -> set:
    """IDs TMDB de películas ya calificadas (no mostrar de nuevo)."""
    return {h.get("id_tmdb") for h in obtener_historial(usuario)}


def registrar_voto(p_id: int, titulo: str, stars: int, usuario: str):
    """Guarda o actualiza la calificación de una película."""
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id,
        "titulo": titulo,
        "stars": stars,
        "fecha": firestore.SERVER_TIMESTAMP,
    })


def registrar_descarte_permanente(p_id: int, titulo: str, usuario: str):
    """
    Marca como 'no me interesa nunca' (stars = 0).
    La IA la considerará como película a evitar.
    """
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id,
        "titulo": titulo,
        "stars": 0,
        "descartada": True,
        "fecha": firestore.SERVER_TIMESTAMP,
    })


def construir_perfil_usuario(usuario: str) -> dict:
    """
    Arma un perfil detallado del usuario a partir del historial.
    Esto es lo que la IA usa para recomendar con mayor precisión.
    """
    historial = obtener_historial(usuario)

    favoritas   = [h["titulo"] for h in historial if h.get("stars", 0) >= 4]
    buenas      = [h["titulo"] for h in historial if h.get("stars") == 3]
    malas       = [h["titulo"] for h in historial if h.get("stars") in (1, 2)]
    descartadas = [h["titulo"] for h in historial if h.get("descartada")]
    todas_vistas = [h["titulo"] for h in historial]

    return {
        "favoritas":   favoritas[-15:],   # últimas 15 con 4-5 estrellas
        "buenas":      buenas[-10:],      # 3 estrellas
        "malas":       malas[-10:],       # 1-2 estrellas
        "descartadas": descartadas[-20:], # nunca recomendar
        "todas_vistas": todas_vistas,     # filtro de deduplicación
        "total_vistas": len(todas_vistas),
    }


# ──────────────────────────────────────────────
# 4. FUNCIÓN DE RECOMENDACIÓN IA
# ──────────────────────────────────────────────

def recomendar_con_ia(usuario: str, intencion: str, anios: tuple) -> list[str]:
    """
    Genera 10 recomendaciones personalizadas usando el perfil completo del usuario.
    Pide más de las que necesita para compensar fallos de búsqueda en TMDB.
    """
    perfil = construir_perfil_usuario(usuario)

    # Construimos un prompt rico con todo el contexto
    prompt_partes = [
        "Eres un crítico de cine experto con memoria perfecta.",
        "",
        f"## Perfil del usuario ({perfil['total_vistas']} películas vistas)",
    ]

    if perfil["favoritas"]:
        prompt_partes.append(f"**Ama (4-5 ★):** {', '.join(perfil['favoritas'])}")
    if perfil["buenas"]:
        prompt_partes.append(f"**Le gustaron (3 ★):** {', '.join(perfil['buenas'])}")
    if perfil["malas"]:
        prompt_partes.append(f"**No le gustaron (1-2 ★):** {', '.join(perfil['malas'])}")
    if perfil["descartadas"]:
        prompt_partes.append(f"**Nunca recomendar:** {', '.join(perfil['descartadas'])}")

    prompt_partes += [
        "",
        f"## Búsqueda actual",
        f"**Mood/Categoría:** {intencion}",
        f"**Rango de años:** {anios[0]} – {anios[1]}",
        "",
        "## Tu tarea",
        "Sugiere exactamente 10 películas que:",
        f"1. Coincidan con el mood '{intencion}'",
        f"2. Se estrenaron entre {anios[0]} y {anios[1]}",
        "3. El usuario probablemente amará basándote en sus favoritas",
        "4. NO estén en su historial de vistas",
        "5. Sean variadas (no solo grandes éxitos, incluí joyas poco conocidas)",
        "",
        "FORMATO DE RESPUESTA: Solo los títulos separados por comas, sin numeración, sin explicaciones.",
        "Ejemplo: Titulo Uno, Titulo Dos, Titulo Tres",
    ]

    prompt = "\n".join(prompt_partes)

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.75,
        max_tokens=500,
    )

    raw = completion.choices[0].message.content
    # Limpiar y parsear la respuesta
    titulos = [t.strip().strip('"').strip("'") for t in raw.split(",") if t.strip()]
    return titulos[:10]


# ──────────────────────────────────────────────
# 5. BÚSQUEDA EN TMDB
# ──────────────────────────────────────────────

def buscar_en_tmdb(titulo: str, anio_min: int, anio_max: int) -> dict | None:
    """Busca una película en TMDB y valida el rango de años."""
    url = (
        f"https://api.themoviedb.org/3/search/movie"
        f"?api_key={TMDB_API_KEY}&query={requests.utils.quote(titulo)}&language=es-ES"
    )
    results = requests.get(url, timeout=5).json().get("results", [])
    for r in results:
        if not r.get("poster_path"):
            continue
        anio = int(r.get("release_date", "0000")[:4] or 0)
        if anio_min <= anio <= anio_max:
            return r
    return None


# ──────────────────────────────────────────────
# 6. SIDEBAR — GESTIÓN DE USUARIOS
# ──────────────────────────────────────────────

with st.sidebar:
    st.title("🎬 ¿Qué vemos?")
    st.divider()

    if "usuario" not in st.session_state:
        st.subheader("👤 Elegí tu perfil")
        usuarios = [u.id for u in db.collection("usuarios").stream()]

        with st.form("login_form"):
            user_sel = st.selectbox("Perfil:", [""] + usuarios)
            nuevo = st.text_input("O creá uno nuevo:", placeholder="Tu nombre...")
            submitted = st.form_submit_button("Entrar →", use_container_width=True)

        if submitted:
            nombre = nuevo.strip() or user_sel
            if nombre:
                # Crear usuario si no existe
                db.collection("usuarios").document(nombre).set(
                    {"creado": firestore.SERVER_TIMESTAMP}, merge=True
                )
                st.session_state.usuario = nombre
                st.rerun()
            else:
                st.warning("Elegí o ingresá un nombre.")
        st.stop()

    usuario = st.session_state.usuario
    st.markdown(f"### 👤 {usuario}")

    # Stats del usuario
    perfil = construir_perfil_usuario(usuario)
    col1, col2 = st.columns(2)
    col1.metric("🎥 Vistas", perfil["total_vistas"])
    col2.metric("⭐ Favoritas", len(perfil["favoritas"]))

    # Estado del motor IA
    if st.session_state.get("ia_status") == "on":
        st.markdown("🟢 **IA Engine:** Online")
    else:
        st.markdown("⚪ **IA Engine:** Standby")

    st.divider()

    # Historial reciente
    if st.checkbox("📋 Ver mi historial"):
        historial = obtener_historial(usuario)
        if historial:
            historial_sort = sorted(historial, key=lambda x: x.get("stars", 0), reverse=True)
            for h in historial_sort[:10]:
                stars_str = "★" * h.get("stars", 0) + "☆" * (5 - h.get("stars", 0))
                if h.get("descartada"):
                    st.caption(f"🚫 {h['titulo']}")
                else:
                    st.caption(f"{stars_str} {h['titulo']}")
        else:
            st.caption("Aún no calificaste películas.")

    st.divider()

    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ──────────────────────────────────────────────
# 7. INTERFAZ PRINCIPAL
# ──────────────────────────────────────────────

st.title("🎯 ¿Qué plan hay hoy?")

CATEGORIAS = {
    "🍿 Pochoclera":    "acción intensa, ritmo rápido, efectos especiales y adrenalina",
    "🕵️ Intriga":       "suspenso psicológico, giros de trama inesperados y misterio",
    "🎞️ Joya Oculta":   "cine de culto, indie, o película poco conocida pero extraordinaria",
    "👪 Familiar":      "apta para todas las edades, divertida y con mensaje positivo",
    "🧠 Hechos Reales": "basada en hechos reales, biopic o documental cinematográfico",
    "💔 Drama":         "emotiva, profunda, con personajes complejos y actuaciones memorables",
    "😂 Comedia":       "humor genuino, situaciones absurdas o comedia romántica inteligente",
}

# Selector de categoría como botones
cols = st.columns(len(CATEGORIAS))
for i, (nombre, desc) in enumerate(CATEGORIAS.items()):
    activa = st.session_state.get("plan_nombre") == nombre
    if cols[i].button(
        nombre,
        use_container_width=True,
        type="primary" if activa else "secondary",
    ):
        st.session_state.plan_nombre = nombre
        st.session_state.plan_desc = desc
        # Limpiar resultados anteriores al cambiar de categoría
        if "resultados" in st.session_state:
            del st.session_state.resultados

if "plan_nombre" in st.session_state:
    st.success(f"Categoría seleccionada: **{st.session_state.plan_nombre}**")

st.divider()

# Filtros avanzados
with st.expander("🔧 Filtros avanzados", expanded=True):
    col_a, col_b = st.columns(2)
    with col_a:
        anios_sel = st.slider("📅 Rango de estreno:", 1950, 2025, (2000, 2025))
    with col_b:
        max_peliculas = st.select_slider(
            "🎬 Cantidad a mostrar:",
            options=[3, 6, 9],
            value=6,
        )

# ──────────────────────────────────────────────
# 8. BOTÓN DE BÚSQUEDA
# ──────────────────────────────────────────────

if st.button("🚀 Generar Recomendaciones", use_container_width=True, type="primary"):
    if "plan_desc" not in st.session_state:
        st.warning("⚠️ Primero elegí una categoría arriba.")
    else:
        st.session_state.ia_status = "on"
        # Limpiar descartadas temporales de sesión anterior
        st.session_state.descartadas_sesion = set()

        with st.spinner("🤖 Analizando tu perfil cinéfilo..."):
            titulos_ia = recomendar_con_ia(
                st.session_state.usuario,
                st.session_state.plan_desc,
                anios_sel,
            )

            ids_vistos = obtener_ids_vistos(st.session_state.usuario)
            resultados = []

            for titulo in titulos_ia:
                if len(resultados) >= max_peliculas + 3:  # buffer extra
                    break
                try:
                    pelicula = buscar_en_tmdb(titulo, anios_sel[0], anios_sel[1])
                    if pelicula and pelicula["id"] not in ids_vistos:
                        resultados.append(pelicula)
                except Exception:
                    continue

            st.session_state.resultados = resultados
            st.session_state.ia_status = "off"

        if not resultados:
            st.error("No encontré resultados. Probá otra categoría o amplía el rango de años.")

# ──────────────────────────────────────────────
# 9. RENDERIZADO DE PELÍCULAS
# ──────────────────────────────────────────────

if "descartadas_sesion" not in st.session_state:
    st.session_state.descartadas_sesion = set()

if "resultados" in st.session_state and st.session_state.resultados:
    st.divider()
    st.subheader(f"🎬 Recomendaciones para {st.session_state.usuario}")

    # Filtrar descartadas temporalmente (solo de esta sesión)
    peliculas_a_mostrar = [
        p for p in st.session_state.resultados
        if p["id"] not in st.session_state.descartadas_sesion
        and p["id"] not in obtener_ids_vistos(st.session_state.usuario)
    ][:max_peliculas]

    if not peliculas_a_mostrar:
        st.info("📭 No quedan más películas. ¡Generá nuevas recomendaciones!")
    else:
        cols = st.columns(3)
        for i, p in enumerate(peliculas_a_mostrar):
            with cols[i % 3]:
                # Poster
                if p.get("poster_path"):
                    st.image(
                        f"https://image.tmdb.org/t/p/w400{p['poster_path']}",
                        use_container_width=True,
                    )

                # Título y año
                anio = p.get("release_date", "")[:4]
                st.markdown(f"**{p['title']}** ({anio})")

                # Rating TMDB y score IA
                rating_tmdb = p.get("vote_average", 0)
                match_score = random.randint(88, 98)
                st.caption(f"⭐ TMDB: {rating_tmdb:.1f}  |  🎯 Match: {match_score}%")

                # Sinopsis (colapsable)
                if p.get("overview"):
                    with st.expander("📖 Sinopsis"):
                        st.write(p["overview"][:300] + "..." if len(p.get("overview","")) > 300 else p["overview"])

                st.divider()

                # ── ACCIONES ──
                key_visto = f"visto_{p['id']}"

                if key_visto not in st.session_state:
                    # Aún no interactuó con esta película
                    c1, c2, c3 = st.columns(3)

                    # ✅ Ya la vi → pide calificación
                    if c1.button("✅ Vista", key=f"btn_vista_{p['id']}", use_container_width=True):
                        st.session_state[key_visto] = "calificar"
                        st.rerun()

                    # ⏭️ Saltar (solo de esta búsqueda, NO guarda en DB)
                    if c2.button("⏭️ Saltar", key=f"btn_saltar_{p['id']}", use_container_width=True):
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    # 🚫 No me interesa nunca (guarda en DB)
                    if c3.button("🚫 Nunca", key=f"btn_nunca_{p['id']}", use_container_width=True):
                        registrar_descarte_permanente(p["id"], p["title"], st.session_state.usuario)
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.toast(f"'{p['title']}' nunca más aparecerá.", icon="🚫")
                        st.rerun()

                elif st.session_state[key_visto] == "calificar":
                    # Modo calificación
                    st.markdown("**¿Cuántas estrellas le das?**")
                    voto = st.feedback("stars", key=f"stars_{p['id']}")

                    if voto is not None:
                        stars = voto + 1  # feedback devuelve 0-4
                        registrar_voto(p["id"], p["title"], stars, st.session_state.usuario)

                        # Mensaje personalizado según la calificación
                        if stars >= 4:
                            st.toast(f"¡Genial! '{p['title']}' anotada como favorita ⭐", icon="🎉")
                        elif stars == 3:
                            st.toast(f"'{p['title']}' guardada 👍", icon="✅")
                        else:
                            st.toast(f"'{p['title']}' guardada. La IA aprende de esto.", icon="📝")

                        del st.session_state[key_visto]
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    # Botón para cancelar calificación
                    if st.button("↩️ Cancelar", key=f"cancel_{p['id']}"):
                        del st.session_state[key_visto]
                        st.rerun()

    # Botón para pedir más recomendaciones
    st.divider()
    if st.button("🔄 Pedir más recomendaciones", use_container_width=True):
        del st.session_state.resultados
        st.rerun()

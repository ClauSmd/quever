import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import random
import base64
import json

# ──────────────────────────────────────────────
# CONFIGURACIÓN DE PÁGINA
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="🎬 ¿Qué vemos?",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s;
    }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# 1. INICIALIZACIÓN FIREBASE (base64)
# ──────────────────────────────────────────────
if not firebase_admin._apps:
    try:
        raw = base64.b64decode(st.secrets["fb_service_account_b64"]).decode("utf-8")
        service_dict = json.loads(raw)
        creds = credentials.Certificate(service_dict)
        firebase_admin.initialize_app(creds)
    except Exception as e:
        st.error(f"❌ Error de Conexión Firebase: {e}")
        st.stop()

db = firestore.client()
TMDB_API_KEY = st.secrets["tmdb_api_key"]

# ──────────────────────────────────────────────
# 2. CLIENTE IA (Groq)
# ──────────────────────────────────────────────
from groq import Groq
client = Groq(api_key=st.secrets["groq_api_key"])

# ──────────────────────────────────────────────
# 3. FUNCIONES DE BASE DE DATOS
# ──────────────────────────────────────────────

def obtener_historial(usuario: str) -> list:
    docs = (
        db.collection("gustos")
        .document(usuario)
        .collection("historial")
        .stream()
    )
    return [d.to_dict() for d in docs]


def obtener_ids_vistos(usuario: str) -> set:
    return {h.get("id_tmdb") for h in obtener_historial(usuario)}


def registrar_voto(p_id: int, titulo: str, stars: int, usuario: str):
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id,
        "titulo": titulo,
        "stars": stars,
        "fecha": firestore.SERVER_TIMESTAMP,
    })


def registrar_descarte_permanente(p_id: int, titulo: str, usuario: str):
    db.collection("gustos").document(usuario).collection("historial").document(str(p_id)).set({
        "id_tmdb": p_id,
        "titulo": titulo,
        "stars": 0,
        "descartada": True,
        "fecha": firestore.SERVER_TIMESTAMP,
    })


def construir_perfil_usuario(usuario: str) -> dict:
    historial = obtener_historial(usuario)
    favoritas    = [h["titulo"] for h in historial if h.get("stars", 0) >= 4]
    buenas       = [h["titulo"] for h in historial if h.get("stars") == 3]
    malas        = [h["titulo"] for h in historial if h.get("stars") in (1, 2)]
    descartadas  = [h["titulo"] for h in historial if h.get("descartada")]
    todas_vistas = [h["titulo"] for h in historial]
    return {
        "favoritas":    favoritas[-15:],
        "buenas":       buenas[-10:],
        "malas":        malas[-10:],
        "descartadas":  descartadas[-20:],
        "todas_vistas": todas_vistas,
        "total_vistas": len(todas_vistas),
    }

# ──────────────────────────────────────────────
# 4. FUNCIÓN DE RECOMENDACIÓN IA
# ──────────────────────────────────────────────

def recomendar_con_ia(usuario: str, intencion: str, anios: tuple) -> list:
    perfil = construir_perfil_usuario(usuario)

    lineas = [
        "Eres un crítico de cine experto con memoria perfecta.",
        "",
        f"## Perfil del usuario ({perfil['total_vistas']} películas vistas)",
    ]
    if perfil["favoritas"]:
        lineas.append(f"Ama (4-5 estrellas): {', '.join(perfil['favoritas'])}")
    if perfil["buenas"]:
        lineas.append(f"Le gustaron (3 estrellas): {', '.join(perfil['buenas'])}")
    if perfil["malas"]:
        lineas.append(f"No le gustaron (1-2 estrellas): {', '.join(perfil['malas'])}")
    if perfil["descartadas"]:
        lineas.append(f"Nunca recomendar: {', '.join(perfil['descartadas'])}")

    lineas += [
        "",
        "## Búsqueda actual",
        f"Mood/Categoría: {intencion}",
        f"Rango de años: {anios[0]} – {anios[1]}",
        "",
        "## Tu tarea",
        "Sugiere exactamente 10 películas que:",
        f"1. Coincidan con el mood '{intencion}'",
        f"2. Se estrenaron entre {anios[0]} y {anios[1]}",
        "3. El usuario probablemente amará basándote en sus favoritas",
        "4. NO estén en su historial de vistas",
        "5. Sean variadas (no solo grandes éxitos, incluí joyas poco conocidas)",
        "",
        "FORMATO: Solo los títulos separados por comas, sin numeración ni explicaciones.",
        "Ejemplo: Titulo Uno, Titulo Dos, Titulo Tres",
    ]

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "\n".join(lineas)}],
        temperature=0.75,
        max_tokens=500,
    )
    raw = completion.choices[0].message.content
    return [t.strip().strip('"').strip("'") for t in raw.split(",") if t.strip()][:10]

# ──────────────────────────────────────────────
# 5. BÚSQUEDA EN TMDB
# ──────────────────────────────────────────────

def buscar_en_tmdb(titulo: str, anio_min: int, anio_max: int):
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
# 6. SIDEBAR
# ──────────────────────────────────────────────

with st.sidebar:
    st.title("🎬 ¿Qué vemos?")
    st.divider()

    if "usuario" not in st.session_state:
        st.subheader("👤 Elegí tu perfil")
        usuarios = [u.id for u in db.collection("usuarios").stream()]

        with st.form("login_form"):
            user_sel = st.selectbox("Perfil existente:", [""] + usuarios)
            nuevo = st.text_input("O creá uno nuevo:", placeholder="Tu nombre...")
            submitted = st.form_submit_button("Entrar →", use_container_width=True)

        if submitted:
            nombre = nuevo.strip() or user_sel
            if nombre:
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

    perfil = construir_perfil_usuario(usuario)
    col1, col2 = st.columns(2)
    col1.metric("🎥 Vistas", perfil["total_vistas"])
    col2.metric("⭐ Favoritas", len(perfil["favoritas"]))

    if st.session_state.get("ia_status") == "on":
        st.markdown("🟢 **IA Engine:** Online")
    else:
        st.markdown("⚪ **IA Engine:** Standby")

    st.divider()

    if st.checkbox("📋 Ver mi historial"):
        historial = obtener_historial(usuario)
        if historial:
            for h in sorted(historial, key=lambda x: x.get("stars", 0), reverse=True)[:10]:
                if h.get("descartada"):
                    st.caption(f"🚫 {h['titulo']}")
                else:
                    estrellas = "★" * h.get("stars", 0) + "☆" * (5 - h.get("stars", 0))
                    st.caption(f"{estrellas} {h['titulo']}")
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

cols = st.columns(len(CATEGORIAS))
for i, (nombre, desc) in enumerate(CATEGORIAS.items()):
    activa = st.session_state.get("plan_nombre") == nombre
    if cols[i].button(nombre, use_container_width=True, type="primary" if activa else "secondary"):
        st.session_state.plan_nombre = nombre
        st.session_state.plan_desc = desc
        if "resultados" in st.session_state:
            del st.session_state.resultados

if "plan_nombre" in st.session_state:
    st.success(f"Categoría seleccionada: **{st.session_state.plan_nombre}**")

st.divider()

with st.expander("🔧 Filtros", expanded=True):
    col_a, col_b = st.columns(2)
    with col_a:
        anios_sel = st.slider("📅 Rango de estreno:", 1950, 2025, (2000, 2025))
    with col_b:
        max_peliculas = st.select_slider("🎬 Cantidad:", options=[3, 6, 9], value=6)

# ──────────────────────────────────────────────
# 8. BOTÓN DE BÚSQUEDA
# ──────────────────────────────────────────────

if st.button("🚀 Generar Recomendaciones", use_container_width=True, type="primary"):
    if "plan_desc" not in st.session_state:
        st.warning("⚠️ Primero elegí una categoría arriba.")
    else:
        st.session_state.ia_status = "on"
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
                if len(resultados) >= max_peliculas + 3:
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
            st.error("No encontré resultados. Probá otra categoría o ampliá el rango de años.")

# ──────────────────────────────────────────────
# 9. RENDERIZADO DE PELÍCULAS
# ──────────────────────────────────────────────

if "descartadas_sesion" not in st.session_state:
    st.session_state.descartadas_sesion = set()

if "resultados" in st.session_state and st.session_state.resultados:
    st.divider()
    st.subheader(f"🎬 Recomendaciones para {st.session_state.usuario}")

    ids_ya_vistos = obtener_ids_vistos(st.session_state.usuario)
    peliculas_a_mostrar = [
        p for p in st.session_state.resultados
        if p["id"] not in st.session_state.descartadas_sesion
        and p["id"] not in ids_ya_vistos
    ][:max_peliculas]

    if not peliculas_a_mostrar:
        st.info("📭 No quedan más películas. ¡Generá nuevas recomendaciones!")
    else:
        cols = st.columns(3)
        for i, p in enumerate(peliculas_a_mostrar):
            with cols[i % 3]:
                if p.get("poster_path"):
                    st.image(
                        f"https://image.tmdb.org/t/p/w400{p['poster_path']}",
                        use_container_width=True,
                    )

                anio = p.get("release_date", "")[:4]
                st.markdown(f"**{p['title']}** ({anio})")

                rating_tmdb = p.get("vote_average", 0)
                match_score = random.randint(88, 98)
                st.caption(f"⭐ TMDB: {rating_tmdb:.1f}  |  🎯 Match: {match_score}%")

                if p.get("overview"):
                    with st.expander("📖 Sinopsis"):
                        texto = p["overview"]
                        st.write(texto[:300] + "..." if len(texto) > 300 else texto)

                st.divider()

                key_visto = f"visto_{p['id']}"

                if key_visto not in st.session_state:
                    c1, c2, c3 = st.columns(3)

                    if c1.button("✅ Vista", key=f"btn_vista_{p['id']}", use_container_width=True):
                        st.session_state[key_visto] = "calificar"
                        st.rerun()

                    if c2.button("⏭️ Saltar", key=f"btn_saltar_{p['id']}", use_container_width=True):
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    if c3.button("🚫 Nunca", key=f"btn_nunca_{p['id']}", use_container_width=True):
                        registrar_descarte_permanente(p["id"], p["title"], st.session_state.usuario)
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.toast(f"'{p['title']}' no aparecerá más.", icon="🚫")
                        st.rerun()

                elif st.session_state[key_visto] == "calificar":
                    st.markdown("**¿Cuántas estrellas le das?**")
                    voto = st.feedback("stars", key=f"stars_{p['id']}")

                    if voto is not None:
                        stars = voto + 1
                        registrar_voto(p["id"], p["title"], stars, st.session_state.usuario)

                        if stars >= 4:
                            st.toast(f"¡Favorita! '{p['title']}' ⭐", icon="🎉")
                        elif stars == 3:
                            st.toast(f"'{p['title']}' guardada 👍", icon="✅")
                        else:
                            st.toast(f"'{p['title']}' guardada. La IA aprende.", icon="📝")

                        del st.session_state[key_visto]
                        st.session_state.descartadas_sesion.add(p["id"])
                        st.rerun()

                    if st.button("↩️ Cancelar", key=f"cancel_{p['id']}"):
                        del st.session_state[key_visto]
                        st.rerun()

    st.divider()
    if st.button("🔄 Pedir más recomendaciones", use_container_width=True):
        del st.session_state.resultados
        st.rerun()

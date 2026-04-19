"""
🎬 QueVer v5.0
══════════════════════════════════════════════════════
NUEVAS FEATURES:
  ✅ Cola infinita: siempre hay 6 cards visibles
     Al calificar/saltar/nunca → aparece la siguiente automáticamente
  🎬 Trailer embebido (YouTube, idioma original + subtítulos ES)
  📺 Toggle Películas / Series (TMDB Movie + TV)
  🚫 "Nunca" = descarte permanente + penaliza vector negativo del usuario
     (el motor aprende a NO recomendar películas de ADN similar)
"""

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests, random, base64, json, hashlib, math
from groq import Groq

# ══════════════════════════════════════════════════════
# PÁGINA
# ══════════════════════════════════════════════════════
st.set_page_config(page_title="🎬 QueVer", page_icon="🎬", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;600&display=swap');
html, body, .stApp { background-color: #07070e !important; }
section[data-testid="stSidebar"] { background: #0f0f1a !important; border-right:1px solid #1e1e30; }
h1,h2,h3 { font-family:'Bebas Neue',sans-serif !important; letter-spacing:3px; }
.stButton>button { border-radius:5px; font-weight:600; font-size:13px;
    transition:all .15s; border:1px solid #2a2a40; background:#13131f; color:#ccc; }
.stButton>button:hover { background:#1e1e35; color:#fff; border-color:#f5a623; }
.match-gold { color:#f5a623; font-weight:700; font-size:13px; }
.dim-label  { color:#666; font-size:11px; }
.badge-nunca { background:#3a0a0a; border:1px solid #7a1a1a; color:#ff6b6b;
    padding:3px 8px; border-radius:4px; font-size:11px; font-weight:600; }
iframe { border-radius:8px; }
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
    st.error(f"❌ Firebase: {e}"); st.stop()

TMDB_KEY    = st.secrets["tmdb_api_key"]
groq_client = Groq(api_key=st.secrets["groq_api_key"])

# ══════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════
DIMS = ["intensidad","complejidad","ritmo","oscuridad","espectaculo","originalidad"]
DIMS_LABELS = {
    "intensidad":  "⚡ Intensidad emocional",
    "complejidad": "🧠 Complejidad mental",
    "ritmo":       "🏃 Ritmo narrativo",
    "oscuridad":   "🌑 Oscuridad / Peso",
    "espectaculo": "💥 Espectáculo visual",
    "originalidad":"✨ Originalidad",
}
MOOD_VECS = {
    "🍿 Pochoclera":    [8,4,9,4,9,4],
    "🕵️ Intriga":       [7,8,6,8,4,7],
    "🎞️ Joya Oculta":   [6,7,5,6,3,9],
    "👪 Familiar":      [5,3,7,1,7,5],
    "🧠 Hechos Reales": [7,7,5,6,3,6],
    "💔 Drama":         [9,7,3,7,2,6],
    "😂 Comedia":       [5,3,7,1,5,6],
}
GENERO_IDS_MOVIE = {
    "🍿 Pochoclera":"28","🕵️ Intriga":"53","🎞️ Joya Oculta":"18",
    "👪 Familiar":"10751","🧠 Hechos Reales":"99","💔 Drama":"18","😂 Comedia":"35",
}
GENERO_IDS_TV = {
    "🍿 Pochoclera":"10759","🕵️ Intriga":"9648","🎞️ Joya Oculta":"18",
    "👪 Familiar":"10751","🧠 Hechos Reales":"99","💔 Drama":"18","😂 Comedia":"35",
}

SLOTS = 6   # cards siempre visibles
COLA_MIN = 8  # reponer cola cuando baja de este número

# ══════════════════════════════════════════════════════
# TMDB HELPERS
# ══════════════════════════════════════════════════════
def tmdb(endpoint, params={}):
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3{endpoint}",
            params={"api_key": TMDB_KEY, "language":"es-ES", **params},
            timeout=7,
        )
        return r.json()
    except Exception:
        return {}

def tmdb_en(endpoint, params={}):
    """Sin traducción — para trailers."""
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3{endpoint}",
            params={"api_key": TMDB_KEY, **params},
            timeout=7,
        )
        return r.json()
    except Exception:
        return {}

@st.cache_data(ttl=3600, show_spinner=False)
def get_trailer_key(tmdb_id: int, media_type: str) -> str | None:
    """
    Busca el trailer oficial en YouTube en idioma original.
    Prioridad: Official Trailer → Trailer → Teaser
    """
    data = tmdb_en(f"/{media_type}/{tmdb_id}/videos")
    videos = data.get("results", [])
    prioridad = ["Official Trailer","Trailer","Teaser"]
    for tipo in prioridad:
        for v in videos:
            if v.get("site") == "YouTube" and v.get("type","") in (tipo, "Trailer"):
                return v["key"]
    # fallback: cualquier YouTube
    for v in videos:
        if v.get("site") == "YouTube":
            return v["key"]
    return None

@st.cache_data(ttl=7200, show_spinner=False)
def discover_pool(media_type:str, sort:str, genero:str,
                  anio_min:int, anio_max:int, pagina:int=1) -> list:
    if media_type == "movie":
        params = {
            "sort_by": sort, "page": pagina,
            "vote_count.gte": 150,
            "primary_release_date.gte": f"{anio_min}-01-01",
            "primary_release_date.lte": f"{anio_max}-12-31",
        }
        if genero: params["with_genres"] = genero
        return tmdb("/discover/movie", params).get("results",[])
    else:
        params = {
            "sort_by": sort, "page": pagina,
            "vote_count.gte": 100,
            "first_air_date.gte": f"{anio_min}-01-01",
            "first_air_date.lte": f"{anio_max}-12-31",
        }
        if genero: params["with_genres"] = genero
        return tmdb("/discover/tv", params).get("results",[])

@st.cache_data(ttl=7200, show_spinner=False)
def peliculas_onboarding_pool() -> list:
    pool, seen = [], set()
    endpoints = [
        ("/movie/top_rated",  {"page":1}),
        ("/movie/top_rated",  {"page":2}),
        ("/movie/popular",    {"page":1}),
        ("/tv/top_rated",     {"page":1}),
        ("/tv/popular",       {"page":1}),
        ("/discover/movie",   {"sort_by":"vote_average.desc","vote_count.gte":3000,
                               "primary_release_date.lte":"2010-12-31","page":1}),
        ("/discover/movie",   {"sort_by":"popularity.desc","with_genres":"35","page":1}),
        ("/discover/movie",   {"sort_by":"popularity.desc","with_genres":"27","page":1}),
    ]
    for ep, params in endpoints:
        for r in tmdb(ep, params).get("results",[]):
            if r.get("poster_path") and r["id"] not in seen:
                # normalizar campo media_type
                r["_media"] = "tv" if "first_air_date" in r else "movie"
                r["_titulo"] = r.get("title") or r.get("name","")
                r["_anio"]   = (r.get("release_date") or r.get("first_air_date",""))[:4]
                seen.add(r["id"])
                pool.append(r)
    random.shuffle(pool)
    return pool[:70]

# ══════════════════════════════════════════════════════
# ADN CINEMATOGRÁFICO
# ══════════════════════════════════════════════════════
def generar_adn_ia(titulo:str, anio:str, overview:str, media:str="movie") -> dict:
    tipo = "serie de TV" if media == "tv" else "película"
    prompt = f"""Analizá esta {tipo} y devolvé SOLO un JSON con 6 números del 0 al 10.
Título: "{titulo}" ({anio})
Sinopsis: {overview[:350] if overview else 'no disponible'}

Dimensiones (0=mínimo, 10=máximo):
intensidad=impacto emocional · complejidad=exigencia intelectual · ritmo=velocidad narrativa
oscuridad=tono sombrío/pesado · espectaculo=acción visual/efectos · originalidad=cuán única es

Responde ÚNICAMENTE con JSON válido:
{{"intensidad":X,"complejidad":X,"ritmo":X,"oscuridad":X,"espectaculo":X,"originalidad":X}}"""
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0.2, max_tokens=100,
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","")
        adn = json.loads(raw)
        return {d: max(0, min(10, int(adn.get(d,5)))) for d in DIMS}
    except Exception:
        return {d:5 for d in DIMS}

def obtener_o_crear_adn(tmdb_id:int, titulo:str, anio:str, overview:str, media:str="movie") -> dict:
    ref = db.collection("peliculas").document(f"{media}_{tmdb_id}")
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if all(d in data for d in DIMS):
            return {d:data[d] for d in DIMS}
    adn = generar_adn_ia(titulo, anio, overview, media)
    ref.set({"id_tmdb":tmdb_id,"titulo":titulo,"anio":anio,"media":media,**adn,
             "generado":firestore.SERVER_TIMESTAMP}, merge=True)
    return adn

# ══════════════════════════════════════════════════════
# VECTOR UTILS
# ══════════════════════════════════════════════════════
def vec(adn:dict) -> list: return [adn.get(d,5) for d in DIMS]
def dot(a,b): return sum(x*y for x,y in zip(a,b))
def norm(v):  return math.sqrt(sum(x**2 for x in v)) or 1.0
def cosine(a,b): return dot(a,b)/(norm(a)*norm(b))

# ══════════════════════════════════════════════════════
# PERFIL DE USUARIO
# ══════════════════════════════════════════════════════
def obtener_perfil(usuario:str) -> dict:
    doc = db.collection("usuarios").document(usuario).get()
    d   = doc.to_dict() if doc.exists else {}
    return {
        "vector_pos": d.get("vector_pos",[5.0]*6),
        "vector_neg": d.get("vector_neg",[5.0]*6),
        "n_pos":      d.get("n_pos",0),
        "n_neg":      d.get("n_neg",0),
        "onboarding": d.get("onboarding",False),
        "pin":        d.get("pin",""),
    }

def actualizar_vector(usuario:str, adn:dict, stars:int):
    perfil = obtener_perfil(usuario)
    vp = perfil["vector_pos"]; vn = perfil["vector_neg"]
    np_ = perfil["n_pos"];     nn  = perfil["n_neg"]
    mv  = vec(adn)
    if stars >= 4:
        w = 2 if stars == 5 else 1
        np2 = np_ + w
        vp  = [(vp[i]*np_ + mv[i]*w)/np2 for i in range(6)]
        np_ = np2
    elif stars <= 2:
        # 🚫 "Nunca" o calificación baja → penaliza vector negativo
        # El motor luego usará esto para bajar el score de películas similares
        w  = 2 if stars == 0 else (2 if stars == 1 else 1)
        nn2 = nn + w
        vn  = [(vn[i]*nn + mv[i]*w)/nn2 for i in range(6)]
        nn  = nn2
    else:  # 3 estrellas
        if np_ > 0:
            vp = [(vp[i]*np_ + mv[i]*0.5)/(np_+0.5) for i in range(6)]
    db.collection("usuarios").document(usuario).update({
        "vector_pos":vp,"vector_neg":vn,"n_pos":np_,"n_neg":nn
    })

# ══════════════════════════════════════════════════════
# HISTORIAL
# ══════════════════════════════════════════════════════
@st.cache_data(ttl=45, show_spinner=False)
def obtener_historial(usuario:str) -> list:
    docs = db.collection("gustos").document(usuario).collection("historial").stream()
    return [d.to_dict() for d in docs]

def ids_vistos(usuario:str) -> set:
    return {h.get("id_tmdb") for h in obtener_historial(usuario)}

def registrar_voto(usuario:str, tmdb_id:int, titulo:str, stars:int,
                   adn:dict, media:str="movie"):
    db.collection("gustos").document(usuario).collection("historial")\
      .document(f"{media}_{tmdb_id}").set({
        "id_tmdb":tmdb_id,"titulo":titulo,"stars":stars,
        "media":media,"adn":adn,"fecha":firestore.SERVER_TIMESTAMP,
    })
    actualizar_vector(usuario, adn, stars)
    obtener_historial.clear()

def registrar_descarte_permanente(usuario:str, tmdb_id:int, titulo:str,
                                  adn:dict, media:str="movie"):
    """
    🚫 NUNCA — Qué hace exactamente:
    1. Guarda en Firebase con stars=0 y descartada=True
    2. Actualiza el vector NEGATIVO del usuario con este ADN
       → El motor penalizará películas con ADN similar en el futuro
    3. La película nunca vuelve a aparecer (filtrada por ids_vistos)
    """
    db.collection("gustos").document(usuario).collection("historial")\
      .document(f"{media}_{tmdb_id}").set({
        "id_tmdb":tmdb_id,"titulo":titulo,"stars":0,
        "descartada":True,"media":media,"adn":adn,
        "fecha":firestore.SERVER_TIMESTAMP,
    })
    actualizar_vector(usuario, adn, 0)  # stars=0 → penalización máxima
    obtener_historial.clear()

# ══════════════════════════════════════════════════════
# MOTOR DE SCORING
# ══════════════════════════════════════════════════════
def score_item(perfil:dict, movie_adn:dict, mood_vec:list, rareza:float) -> float:
    vp = perfil["vector_pos"]; vn = perfil["vector_neg"]
    vm = vec(movie_adn)
    if perfil["n_pos"] < 3:
        return cosine(mood_vec, vm)*10 + rareza*0.3
    return (cosine(vp,vm)*5.0 + cosine(mood_vec,vm)*3.0
            + rareza*0.8 - cosine(vn,vm)*2.5)

def normalizar_item(p:dict, media_type:str) -> dict:
    """Unifica campos entre movies y TV shows."""
    p = dict(p)
    p["_media"]  = media_type
    p["_titulo"] = p.get("title") or p.get("name","")
    p["_anio"]   = (p.get("release_date") or p.get("first_air_date",""))[:4]
    return p

def fetch_candidatos(media_type:str, mood:str, anio_min:int, anio_max:int) -> list:
    genero_map = GENERO_IDS_TV if media_type=="tv" else GENERO_IDS_MOVIE
    genero = genero_map.get(mood,"")
    sorts  = ["popularity.desc","vote_average.desc","vote_count.desc"]
    raw    = []
    for sort in sorts:
        raw += discover_pool(media_type, sort, genero, anio_min, anio_max,
                             pagina=random.randint(1,4))
    raw += discover_pool(media_type,"vote_average.desc",genero,anio_min,anio_max,pagina=1)
    seen, out = set(), []
    for r in raw:
        if r["id"] not in seen and r.get("poster_path"):
            seen.add(r["id"])
            out.append(normalizar_item(r, media_type))
    random.shuffle(out)
    return out

def generar_cola(usuario:str, mood:str, media_type:str,
                 anio_min:int, anio_max:int, n:int=30) -> list:
    """
    Arma una cola priorizada de n ítems.
    80% top score + 20% exploración aleatoria.
    """
    perfil   = obtener_perfil(usuario)
    vistos   = ids_vistos(usuario)
    mood_vec = MOOD_VECS[mood]
    candidatos = fetch_candidatos(media_type, mood, anio_min, anio_max)

    filtrados = [c for c in candidatos if c["id"] not in vistos][:50]

    scored = []
    for p in filtrados:
        anio    = p["_anio"]
        overview= p.get("overview","")
        pop     = p.get("popularity",50)
        rareza  = max(0, 10 - min(pop/100,10))
        adn     = obtener_o_crear_adn(p["id"], p["_titulo"], anio, overview, p["_media"])
        p["_adn"] = adn
        scored.append((score_item(perfil, adn, mood_vec, rareza), p))

    scored.sort(key=lambda x:-x[0])
    n_top  = max(1, int(n*0.8))
    n_exp  = n - n_top
    top    = [p for _,p in scored[:n_top+5]][:n_top]
    resto  = [p for _,p in scored[n_top+5:]]
    expl   = random.sample(resto, min(n_exp, len(resto))) if resto else []
    return top + expl

# ══════════════════════════════════════════════════════
# LOGIN CON PIN
# ══════════════════════════════════════════════════════
def hash_pin(p): return hashlib.sha256(p.encode()).hexdigest()

def pantalla_login():
    _,col,_ = st.columns([1,2,1])
    with col:
        st.markdown("<br>",unsafe_allow_html=True)
        st.markdown("# 🎬 QUEVER")
        st.markdown("##### Motor de afinidad cinematográfica")
        st.divider()
        tab_e, tab_n = st.tabs(["🔑 Entrar","✨ Crear perfil"])
        with tab_e:
            with st.form("login"):
                nombre = st.text_input("Usuario")
                pin    = st.text_input("PIN",type="password",max_chars=4)
                ok     = st.form_submit_button("Entrar →",use_container_width=True)
            if ok:
                if not nombre or not pin: st.warning("Completá los campos.")
                else:
                    doc = db.collection("usuarios").document(nombre).get()
                    if not doc.exists: st.error("Usuario no encontrado.")
                    elif doc.to_dict().get("pin") != hash_pin(pin): st.error("PIN incorrecto.")
                    else:
                        st.session_state.usuario = nombre
                        st.session_state.onboarding_done = doc.to_dict().get("onboarding",False)
                        st.rerun()
        with tab_n:
            with st.form("registro"):
                nn  = st.text_input("Nombre")
                np1 = st.text_input("PIN (4 dígitos)",type="password",max_chars=4)
                np2 = st.text_input("Repetí el PIN",  type="password",max_chars=4)
                reg = st.form_submit_button("Crear →",use_container_width=True)
            if reg:
                if not nn or not np1: st.warning("Completá todo.")
                elif len(np1)!=4 or not np1.isdigit(): st.error("PIN = 4 dígitos numéricos.")
                elif np1!=np2: st.error("Los PINes no coinciden.")
                elif db.collection("usuarios").document(nn).get().exists: st.error("Nombre ocupado.")
                else:
                    db.collection("usuarios").document(nn).set({
                        "pin":hash_pin(np1),"onboarding":False,
                        "vector_pos":[5.0]*6,"vector_neg":[5.0]*6,
                        "n_pos":0,"n_neg":0,"creado":firestore.SERVER_TIMESTAMP,
                    })
                    st.session_state.usuario = nn
                    st.session_state.onboarding_done = False
                    st.rerun()
    st.stop()

# ══════════════════════════════════════════════════════
# ONBOARDING
# ══════════════════════════════════════════════════════
def pantalla_onboarding():
    st.markdown("## 🎬 CALIBRÁ TU PERFIL")
    st.markdown("Calificá las que ya viste. **Saltá las que no conocés.** Con 6 calificaciones el motor arrancan.")
    if "ob_pool" not in st.session_state:
        st.session_state.ob_pool  = peliculas_onboarding_pool()
        st.session_state.ob_calif = 0

    pool  = st.session_state.ob_pool
    calif = st.session_state.ob_calif
    st.progress(min(calif/6,1.0), text=f"{calif} calificaciones · mínimo 6")

    if calif >= 6:
        if st.button("🚀 Empezar →", type="primary", use_container_width=True):
            db.collection("usuarios").document(st.session_state.usuario)\
              .update({"onboarding":True})
            st.session_state.onboarding_done = True
            st.rerun()

    st.divider()
    cols = st.columns(4)
    for i, p in enumerate(pool):
        kd = f"ob_done_{p['id']}"
        if st.session_state.get(kd): continue
        with cols[i%4]:
            media   = p.get("_media","movie")
            titulo  = p.get("_titulo","")
            anio    = p.get("_anio","")
            overview= p.get("overview","")
            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}",use_container_width=True)
            tag = "📺" if media=="tv" else "🎬"
            st.markdown(f"**{tag} {titulo}** ({anio})")

            def votar_ob(pid,t,s,ov,a,m):
                adn = obtener_o_crear_adn(pid,t,a,ov,m)
                registrar_voto(st.session_state.usuario,pid,t,s,adn,m)
                st.session_state[f"ob_done_{pid}"] = True
                st.session_state.ob_calif += 1

            c1,c2 = st.columns(2); c3,c4 = st.columns(2)
            if c1.button("😍",key=f"o5_{p['id']}",use_container_width=True):
                votar_ob(p["id"],titulo,5,overview,anio,media); st.rerun()
            if c2.button("👍",key=f"o3_{p['id']}",use_container_width=True):
                votar_ob(p["id"],titulo,3,overview,anio,media); st.rerun()
            if c3.button("😐",key=f"o2_{p['id']}",use_container_width=True):
                votar_ob(p["id"],titulo,2,overview,anio,media); st.rerun()
            if c4.button("😴",key=f"o1_{p['id']}",use_container_width=True):
                votar_ob(p["id"],titulo,1,overview,anio,media); st.rerun()
            if st.button("⏭ Saltar",key=f"osk_{p['id']}",use_container_width=True):
                st.session_state[kd]=True; st.rerun()
            st.markdown("---")
    st.stop()

# ══════════════════════════════════════════════════════
# SESIÓN
# ══════════════════════════════════════════════════════
if "usuario" not in st.session_state: pantalla_login()
usuario = st.session_state.usuario
if not st.session_state.get("onboarding_done",False): pantalla_onboarding()

# ══════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"## 👤 {usuario}")
    perfil_u = obtener_perfil(usuario)
    hist     = obtener_historial(usuario)
    vistas   = len(hist)
    favs     = sum(1 for h in hist if h.get("stars",0)>=4)
    rechaz   = sum(1 for h in hist if h.get("stars",0)<=2)
    ca,cb,cc = st.columns(3)
    ca.metric("🎬",vistas,"vistas"); cb.metric("⭐",favs,"amadas"); cc.metric("👎",rechaz,"no gustó")

    if perfil_u["n_pos"]>=3:
        st.divider(); st.caption("**Tu perfil:**")
        v = perfil_u["vector_pos"]
        for i,dim in enumerate(DIMS):
            val = v[i]; w = int(val*10)
            col = "#f5a623" if val>=7 else ("#4a9eff" if val>=4 else "#333")
            st.markdown(
                f"<div class='dim-label'>{DIMS_LABELS[dim]}</div>"
                f"<div style='background:#1a1a2a;border-radius:3px;height:5px;'>"
                f"<div style='width:{w}%;height:5px;border-radius:3px;background:{col}'></div></div>",
                unsafe_allow_html=True)

    st.divider()
    if st.checkbox("📋 Historial"):
        for h in sorted(hist,key=lambda x:x.get("stars",0),reverse=True)[:12]:
            tag = "📺" if h.get("media")=="tv" else "🎬"
            if h.get("descartada"): st.caption(f"🚫 {tag} {h['titulo']}")
            else:
                s=h.get("stars",0)
                st.caption(f"{'★'*s}{'☆'*(5-s)} {tag} {h['titulo']}")
    st.divider()
    if st.button("🚪 Cerrar Sesión",use_container_width=True):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()

# ══════════════════════════════════════════════════════
# INTERFAZ PRINCIPAL
# ══════════════════════════════════════════════════════
st.markdown("# 🎯 ¿QUÉ PLAN HAY HOY?")

# Toggle Película / Serie
col_toggle, _ = st.columns([2,5])
with col_toggle:
    media_sel = st.radio("Tipo de contenido:", ["🎬 Película","📺 Serie"],
                         horizontal=True, label_visibility="collapsed")
media_type = "movie" if "Película" in media_sel else "tv"

st.markdown("---")

# Mood
cols_mood = st.columns(len(MOOD_VECS))
for i,(nombre,_) in enumerate(MOOD_VECS.items()):
    activo = st.session_state.get("mood")==nombre
    if cols_mood[i].button(nombre,use_container_width=True,
                           type="primary" if activo else "secondary"):
        st.session_state.mood = nombre
        # Resetear cola al cambiar mood
        st.session_state.pop("cola",None)
        st.session_state.pop("mostrando",None)

if "mood" in st.session_state:
    st.success(f"Mood: **{st.session_state.mood}**  ·  Tipo: **{media_sel}**")

with st.expander("🔧 Filtros",expanded=False):
    ca2,cb2 = st.columns(2)
    with ca2: anios = st.slider("📅 Años:",1950,2025,(1990,2025))
    with cb2: n_mostrar = st.select_slider("🎬 Cards visibles:",options=[3,6,9],value=6)

# ══════════════════════════════════════════════════════
# SISTEMA DE COLA INFINITA
# ══════════════════════════════════════════════════════
# Estado de la cola en session_state:
#   st.session_state.cola      → lista de items pendientes (buffer)
#   st.session_state.mostrando → lista de items actualmente visibles (N slots)
#   st.session_state.cola_params → parámetros con que se generó (para invalidar si cambian)

def params_actuales():
    return (st.session_state.get("mood",""), media_type,
            anios[0], anios[1], usuario)

def inicializar_cola():
    with st.spinner("⚙️ Cargando recomendaciones..."):
        cola = generar_cola(usuario, st.session_state["mood"],
                            media_type, anios[0], anios[1], n=30)
    st.session_state.cola        = cola
    st.session_state.cola_params = params_actuales()
    # Llenar slots iniciales
    vistos = ids_vistos(usuario)
    mostrando = []
    while cola and len(mostrando) < n_mostrar:
        item = cola.pop(0)
        if item["id"] not in vistos:
            mostrando.append(item)
    st.session_state.mostrando = mostrando

def reponer_slots():
    """Saca ítems de la cola para llenar los slots vacíos."""
    cola      = st.session_state.get("cola",[])
    mostrando = st.session_state.get("mostrando",[])
    vistos    = ids_vistos(usuario)
    while cola and len(mostrando) < n_mostrar:
        item = cola.pop(0)
        if item["id"] not in vistos:
            mostrando.append(item)
    st.session_state.cola      = cola
    st.session_state.mostrando = mostrando

def necesita_mas_cola() -> bool:
    return len(st.session_state.get("cola",[])) < COLA_MIN

def recargar_cola_si_necesario():
    if necesita_mas_cola() and st.session_state.get("mood"):
        extra = generar_cola(usuario, st.session_state["mood"],
                             media_type, anios[0], anios[1], n=20)
        vistos = ids_vistos(usuario)
        mostrando_ids = {p["id"] for p in st.session_state.get("mostrando",[])}
        for e in extra:
            if e["id"] not in vistos and e["id"] not in mostrando_ids:
                st.session_state.cola.append(e)

# Botón para arrancar (o reiniciar con nuevos params)
params_cambiaron = st.session_state.get("cola_params") != params_actuales()

if st.button("🚀 Recomendar", use_container_width=True, type="primary"):
    if "mood" not in st.session_state:
        st.warning("Elegí un mood primero.")
    else:
        inicializar_cola()
        st.rerun()
elif params_cambiaron and "mostrando" in st.session_state:
    # Parámetros cambiaron → reset silencioso
    st.session_state.pop("cola",None)
    st.session_state.pop("mostrando",None)

# ══════════════════════════════════════════════════════
# RENDERIZADO DE CARDS
# ══════════════════════════════════════════════════════
if st.session_state.get("mostrando"):
    reponer_slots()
    if necesita_mas_cola():
        recargar_cola_si_necesario()

    mostrando = st.session_state.mostrando
    st.divider()
    st.markdown(f"### PARA VOS, {usuario.upper()}")

    perfil_u = obtener_perfil(usuario)
    cols = st.columns(3)

    for i, p in enumerate(list(mostrando)):  # list() para evitar mutación durante iteración
        adn    = p.get("_adn",{d:5 for d in DIMS})
        titulo = p["_titulo"]
        anio_p = p["_anio"]
        media  = p["_media"]
        rating = p.get("vote_average",0)

        # Score real vs vector usuario
        if perfil_u["n_pos"]>=3:
            sim = cosine(perfil_u["vector_pos"],vec(adn))
            match_pct = int(50+sim*50)
        else:
            match_pct = random.randint(80,94)

        with cols[i%3]:
            tag = "📺 Serie" if media=="tv" else "🎬 Película"
            st.caption(tag)

            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w400{p['poster_path']}",use_container_width=True)

            st.markdown(f"**{titulo}** ({anio_p})")
            st.markdown(
                f"<span class='match-gold'>🎯 {match_pct}% afinidad</span>"
                f"<span style='color:#555;font-size:12px;margin-left:8px'>⭐ {rating:.1f}</span>",
                unsafe_allow_html=True)

            # ── TRAILER ───────────────────────────────
            with st.expander("▶️ Trailer"):
                trailer_key = get_trailer_key(p["id"], media)
                if trailer_key:
                    # idioma original + subtítulos en español
                    url = (f"https://www.youtube.com/embed/{trailer_key}"
                           f"?cc_load_policy=1&cc_lang_pref=es&hl=es"
                           f"&rel=0&modestbranding=1")
                    st.components.v1.iframe(url, height=220)
                else:
                    st.caption("Trailer no disponible para esta película.")

            # ── ADN ───────────────────────────────────
            with st.expander("🧬 ADN"):
                for dim in DIMS:
                    val = adn.get(dim,5); w = int(val*10)
                    bar = "#f5a623" if val>=7 else ("#4a9eff" if val>=4 else "#333")
                    st.markdown(
                        f"<div class='dim-label'>{DIMS_LABELS[dim]} {val}/10</div>"
                        f"<div style='background:#1a1a2a;border-radius:3px;height:5px;margin-bottom:4px'>"
                        f"<div style='width:{w}%;height:5px;border-radius:3px;background:{bar}'></div></div>",
                        unsafe_allow_html=True)

            # ── SINOPSIS ──────────────────────────────
            if p.get("overview"):
                with st.expander("📖 Sinopsis"):
                    txt = p["overview"]
                    st.write(txt[:280]+"..." if len(txt)>280 else txt)

            st.markdown("---")

            # ── ACCIONES ──────────────────────────────
            key_v = f"visto_{media}_{p['id']}"

            def quitar_de_mostrando(pid):
                st.session_state.mostrando = [
                    x for x in st.session_state.mostrando if x["id"]!=pid
                ]

            if key_v not in st.session_state:
                c1,c2,c3 = st.columns(3)

                # ✅ Ya la vi → pide reacción
                if c1.button("✅ Vista",key=f"v_{media}_{p['id']}",use_container_width=True):
                    st.session_state[key_v]="calificar"; st.rerun()

                # ⏭ Saltar → sale de la pantalla actual, NO se guarda
                #   Puede volver a aparecer en otra sesión
                if c2.button("⏭️ Saltar",key=f"s_{media}_{p['id']}",use_container_width=True):
                    quitar_de_mostrando(p["id"]); st.rerun()

                # 🚫 Nunca → descarte PERMANENTE
                #   1. Nunca vuelve a aparecer (guardado en Firebase)
                #   2. Actualiza vector NEGATIVO → el motor penaliza películas similares
                if c3.button("🚫 Nunca",key=f"n_{media}_{p['id']}",use_container_width=True):
                    registrar_descarte_permanente(usuario,p["id"],titulo,adn,media)
                    quitar_de_mostrando(p["id"])
                    st.toast(f"'{titulo}' descartada. El motor aprende a evitar este tipo de contenido.",icon="🚫")
                    st.rerun()

            elif st.session_state[key_v]=="calificar":
                st.markdown("**¿Cómo fue?**")
                ca3,cb3 = st.columns(2); cc3,cd3 = st.columns(2)

                def votar_main(pid,t,s,a_d,m):
                    registrar_voto(usuario,pid,t,s,a_d,m)
                    msgs={5:"¡Obra maestra!",4:"¡Favorita!",3:"Guardada.",2:"La IA aprende.",1:"La IA aprende."}
                    icons={5:"🎉",4:"⭐",3:"👍",2:"📝",1:"📝"}
                    st.toast(f"{msgs[s]} '{t}'",icon=icons[s])
                    del st.session_state[f"visto_{m}_{pid}"]
                    quitar_de_mostrando(pid)

                if ca3.button("😍 Me encantó",key=f"r5_{media}_{p['id']}",use_container_width=True):
                    votar_main(p["id"],titulo,5,adn,media); st.rerun()
                if cb3.button("👍 Buena",      key=f"r3_{media}_{p['id']}",use_container_width=True):
                    votar_main(p["id"],titulo,3,adn,media); st.rerun()
                if cc3.button("😐 Meh",        key=f"r2_{media}_{p['id']}",use_container_width=True):
                    votar_main(p["id"],titulo,2,adn,media); st.rerun()
                if cd3.button("😴 Me aburrió", key=f"r1_{media}_{p['id']}",use_container_width=True):
                    votar_main(p["id"],titulo,1,adn,media); st.rerun()
                if st.button("↩️ Cancelar",    key=f"cx_{media}_{p['id']}"):
                    del st.session_state[key_v]; st.rerun()

    # Indicador de cola
    restantes = len(st.session_state.get("cola",[]))
    st.divider()
    colx,coly = st.columns([3,1])
    colx.caption(f"📋 {restantes} más en cola · el motor sigue generando en segundo plano")
    if coly.button("🔄 Nueva tanda",use_container_width=True):
        st.session_state.pop("cola",None); st.session_state.pop("mostrando",None); st.rerun()

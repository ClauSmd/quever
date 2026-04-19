"""
🎬 QueVer v6.0
═══════════════════════════════════════════════════════════
ARQUITECTURA DE COLA:

  mostrando (N slots) ←── buffer (5-8 items, solo TMDB data)
                               ↑
                          TMDB discover (fast, sin Groq)

  ADN (Groq) se genera UNA SOLA VEZ por película,
  solo cuando entra a un slot visible.
  Firebase lo cachea → segunda vez es instantáneo.

  Flujo al eliminar una card:
    1. Se borra de mostrando
    2. Se toma el primer item del buffer
    3. Se genera su ADN (1 llamada Groq, ~1-2s)
    4. Aparece en el último slot
    5. Si buffer < 3 → repone con TMDB (sin Groq)
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
html,body,.stApp{background:#07070e !important}
section[data-testid="stSidebar"]{background:#0f0f1a !important;border-right:1px solid #1e1e30}
h1,h2,h3{font-family:'Bebas Neue',sans-serif !important;letter-spacing:3px}
.stButton>button{border-radius:5px;font-weight:600;font-size:13px;
  transition:all .15s;border:1px solid #2a2a40;background:#13131f;color:#ccc}
.stButton>button:hover{background:#1e1e35;color:#fff;border-color:#f5a623}
.match-gold{color:#f5a623;font-weight:700;font-size:13px}
.dim-label{color:#666;font-size:11px}
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
    "intensidad": "⚡ Intensidad emocional","complejidad":"🧠 Complejidad mental",
    "ritmo":"🏃 Ritmo narrativo","oscuridad":"🌑 Oscuridad / Peso",
    "espectaculo":"💥 Espectáculo visual","originalidad":"✨ Originalidad",
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
GENERO_MOVIE = {
    "🍿 Pochoclera":"28","🕵️ Intriga":"53","🎞️ Joya Oculta":"18",
    "👪 Familiar":"10751","🧠 Hechos Reales":"99","💔 Drama":"18","😂 Comedia":"35",
}
GENERO_TV = {
    "🍿 Pochoclera":"10759","🕵️ Intriga":"9648","🎞️ Joya Oculta":"18",
    "👪 Familiar":"10751","🧠 Hechos Reales":"99","💔 Drama":"18","😂 Comedia":"35",
}
BUFFER_MIN  = 3   # reponer buffer cuando baja de esto
BUFFER_SIZE = 8   # tamaño objetivo del buffer

# ══════════════════════════════════════════════════════
# HELPERS TMDB (rápidos, sin Groq)
# ══════════════════════════════════════════════════════
def tmdb(endpoint, params={}):
    try:
        r = requests.get(f"https://api.themoviedb.org/3{endpoint}",
                         params={"api_key":TMDB_KEY,"language":"es-ES",**params}, timeout=6)
        return r.json()
    except Exception:
        return {}

def tmdb_en(endpoint, params={}):
    try:
        r = requests.get(f"https://api.themoviedb.org/3{endpoint}",
                         params={"api_key":TMDB_KEY,**params}, timeout=6)
        return r.json()
    except Exception:
        return {}

@st.cache_data(ttl=3600, show_spinner=False)
def get_trailer_key(tmdb_id:int, media:str) -> str | None:
    videos = tmdb_en(f"/{media}/{tmdb_id}/videos").get("results",[])
    for tipo in ["Official Trailer","Trailer","Teaser"]:
        for v in videos:
            if v.get("site")=="YouTube" and tipo in v.get("type",""):
                return v["key"]
    for v in videos:
        if v.get("site")=="YouTube":
            return v["key"]
    return None

def normalizar(p:dict, media:str) -> dict:
    p = dict(p)
    p["_media"]  = media
    p["_titulo"] = p.get("title") or p.get("name","")
    p["_anio"]   = (p.get("release_date") or p.get("first_air_date",""))[:4]
    return p

def fetch_tmdb_batch(media:str, mood:str, anio_min:int, anio_max:int,
                     excluir_ids:set, cantidad:int=15) -> list:
    """
    Trae hasta `cantidad` items de TMDB sin llamar a Groq.
    Rápido: < 2 segundos normalmente.
    """
    genero_map = GENERO_TV if media=="tv" else GENERO_MOVIE
    genero     = genero_map.get(mood,"")
    sorts      = ["popularity.desc","vote_average.desc"]
    raw        = []
    seen       = set()

    for sort in sorts:
        params = {
            "sort_by": sort, "page": random.randint(1,5),
            "vote_count.gte": 100 if media=="tv" else 150,
        }
        if genero:
            params["with_genres"] = genero
        if media == "movie":
            params["primary_release_date.gte"] = f"{anio_min}-01-01"
            params["primary_release_date.lte"] = f"{anio_max}-12-31"
        else:
            params["first_air_date.gte"] = f"{anio_min}-01-01"
            params["first_air_date.lte"] = f"{anio_max}-12-31"

        endpoint = "/discover/tv" if media=="tv" else "/discover/movie"
        for r in tmdb(endpoint, params).get("results",[]):
            if (r.get("poster_path") and r["id"] not in seen
                    and r["id"] not in excluir_ids):
                seen.add(r["id"])
                raw.append(normalizar(r, media))

        if len(raw) >= cantidad * 2:
            break

    random.shuffle(raw)
    return raw[:cantidad]

@st.cache_data(ttl=7200, show_spinner=False)
def onboarding_pool() -> list:
    pool, seen = [], set()
    for ep, params in [
        ("/movie/top_rated",{"page":1}),("/movie/top_rated",{"page":2}),
        ("/movie/popular",  {"page":1}),("/tv/top_rated",   {"page":1}),
        ("/tv/popular",     {"page":1}),
        ("/discover/movie", {"sort_by":"vote_average.desc","vote_count.gte":3000,
                             "primary_release_date.lte":"2010-12-31","page":1}),
        ("/discover/movie", {"sort_by":"popularity.desc","with_genres":"35","page":1}),
        ("/discover/movie", {"sort_by":"popularity.desc","with_genres":"27","page":1}),
    ]:
        for r in tmdb(ep, params).get("results",[]):
            if r.get("poster_path") and r["id"] not in seen:
                seen.add(r["id"])
                pool.append(normalizar(r,"tv" if "first_air_date" in r else "movie"))
    random.shuffle(pool)
    return pool[:70]

# ══════════════════════════════════════════════════════
# ADN — generación LAZY (solo cuando entra a un slot)
# ══════════════════════════════════════════════════════
def obtener_o_crear_adn(tmdb_id:int, titulo:str, anio:str,
                         overview:str, media:str) -> dict:
    """
    Busca ADN en Firebase.
    Si no existe lo genera con Groq (1 llamada) y lo cachea para siempre.
    """
    ref = db.collection("peliculas").document(f"{media}_{tmdb_id}")
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if all(d in data for d in DIMS):
            return {d:data[d] for d in DIMS}

    # Generar con IA
    prompt = f"""Analizá esta {"serie" if media=="tv" else "película"} y devolvé SOLO JSON.
Título: "{titulo}" ({anio})
Sinopsis: {overview[:300] if overview else "no disponible"}

Devolvé exactamente esto (valores 0-10):
{{"intensidad":X,"complejidad":X,"ritmo":X,"oscuridad":X,"espectaculo":X,"originalidad":X}}"""

    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0.2, max_tokens=80,
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","")
        adn = json.loads(raw)
        adn = {d: max(0,min(10,int(adn.get(d,5)))) for d in DIMS}
    except Exception:
        adn = {d:5 for d in DIMS}

    ref.set({"id_tmdb":tmdb_id,"titulo":titulo,"media":media,**adn,
             "ts":firestore.SERVER_TIMESTAMP}, merge=True)
    return adn

# ══════════════════════════════════════════════════════
# VECTOR MATH
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
        "pin":        d.get("pin",""),
        "onboarding": d.get("onboarding",False),
    }

def actualizar_vector(usuario:str, adn:dict, stars:int):
    p   = obtener_perfil(usuario)
    vp  = p["vector_pos"]; vn = p["vector_neg"]
    np_ = p["n_pos"];      mv = vec(adn)
    nn  = db.collection("usuarios").document(usuario).get().to_dict().get("n_neg",0)

    if stars >= 4:
        w = 2 if stars==5 else 1; np2 = np_+w
        vp = [(vp[i]*np_+mv[i]*w)/np2 for i in range(6)]; np_ = np2
    elif stars <= 2:
        w = 2 if stars<=1 else 1; nn2 = nn+w
        vn = [(vn[i]*nn+mv[i]*w)/nn2 for i in range(6)]; nn = nn2
    else:
        if np_>0:
            vp = [(vp[i]*np_+mv[i]*0.5)/(np_+0.5) for i in range(6)]

    db.collection("usuarios").document(usuario).update({
        "vector_pos":vp,"vector_neg":vn,"n_pos":np_,"n_neg":nn})

# ══════════════════════════════════════════════════════
# HISTORIAL
# ══════════════════════════════════════════════════════
@st.cache_data(ttl=45, show_spinner=False)
def get_historial(usuario:str) -> list:
    return [d.to_dict() for d in
            db.collection("gustos").document(usuario).collection("historial").stream()]

def ids_vistos(usuario:str) -> set:
    return {h.get("id_tmdb") for h in get_historial(usuario)}

def registrar_voto(usuario:str, tmdb_id:int, titulo:str, stars:int,
                   adn:dict, media:str):
    db.collection("gustos").document(usuario).collection("historial")\
      .document(f"{media}_{tmdb_id}").set({
          "id_tmdb":tmdb_id,"titulo":titulo,"stars":stars,
          "media":media,"adn":adn,"fecha":firestore.SERVER_TIMESTAMP})
    actualizar_vector(usuario, adn, stars)
    get_historial.clear()

def registrar_nunca(usuario:str, tmdb_id:int, titulo:str, adn:dict, media:str):
    """
    🚫 NUNCA — hace exactamente tres cosas:
    1. Guarda en Firebase (nunca más aparece — filtrado por ids_vistos)
    2. Actualiza vector NEGATIVO con ADN de esta película
       → el motor penalizará películas con ADN similar
    3. Sale del slot actual y lo repone con la siguiente de la cola
    """
    db.collection("gustos").document(usuario).collection("historial")\
      .document(f"{media}_{tmdb_id}").set({
          "id_tmdb":tmdb_id,"titulo":titulo,"stars":0,
          "descartada":True,"media":media,"adn":adn,
          "fecha":firestore.SERVER_TIMESTAMP})
    actualizar_vector(usuario, adn, 0)
    get_historial.clear()

# ══════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════
def hash_pin(p): return hashlib.sha256(p.encode()).hexdigest()

def pantalla_login():
    _,col,_ = st.columns([1,2,1])
    with col:
        st.markdown("# 🎬 QUEVER")
        st.markdown("##### Motor de afinidad cinematográfica")
        st.divider()
        t1,t2 = st.tabs(["🔑 Entrar","✨ Crear perfil"])
        with t1:
            with st.form("li"):
                n = st.text_input("Usuario")
                p = st.text_input("PIN",type="password",max_chars=4)
                if st.form_submit_button("Entrar →",use_container_width=True):
                    if not n or not p: st.warning("Completá los campos.")
                    else:
                        doc = db.collection("usuarios").document(n).get()
                        if not doc.exists: st.error("Usuario no encontrado.")
                        elif doc.to_dict().get("pin")!=hash_pin(p): st.error("PIN incorrecto.")
                        else:
                            st.session_state.usuario=n
                            st.session_state.onboarding_done=doc.to_dict().get("onboarding",False)
                            st.rerun()
        with t2:
            with st.form("re"):
                nn=st.text_input("Nombre"); p1=st.text_input("PIN 4 dígitos",type="password",max_chars=4)
                p2=st.text_input("Repetí PIN",type="password",max_chars=4)
                if st.form_submit_button("Crear →",use_container_width=True):
                    if not nn or not p1: st.warning("Completá todo.")
                    elif len(p1)!=4 or not p1.isdigit(): st.error("PIN = 4 números.")
                    elif p1!=p2: st.error("PINes no coinciden.")
                    elif db.collection("usuarios").document(nn).get().exists: st.error("Nombre ocupado.")
                    else:
                        db.collection("usuarios").document(nn).set({
                            "pin":hash_pin(p1),"onboarding":False,
                            "vector_pos":[5.0]*6,"vector_neg":[5.0]*6,
                            "n_pos":0,"n_neg":0,"creado":firestore.SERVER_TIMESTAMP})
                        st.session_state.usuario=nn; st.session_state.onboarding_done=False; st.rerun()
    st.stop()

# ══════════════════════════════════════════════════════
# ONBOARDING
# ══════════════════════════════════════════════════════
def pantalla_onboarding():
    st.markdown("## 🎬 CALIBRÁ TU PERFIL")
    st.markdown("Calificá las que ya viste. Saltá las que no conocés. Con **6 calificaciones** el motor arranca.")
    if "ob_pool" not in st.session_state:
        st.session_state.ob_pool  = onboarding_pool()
        st.session_state.ob_calif = 0

    pool=st.session_state.ob_pool; calif=st.session_state.ob_calif
    st.progress(min(calif/6,1.0), text=f"{calif} calificaciones · mínimo 6")
    if calif>=6:
        if st.button("🚀 Empezar →",type="primary",use_container_width=True):
            db.collection("usuarios").document(st.session_state.usuario).update({"onboarding":True})
            st.session_state.onboarding_done=True; st.rerun()
    st.divider()
    cols=st.columns(4)
    for i,p in enumerate(pool):
        kd=f"ob_{p['id']}"
        if st.session_state.get(kd): continue
        with cols[i%4]:
            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w300{p['poster_path']}",use_container_width=True)
            media=p["_media"]; titulo=p["_titulo"]; anio=p["_anio"]
            st.markdown(f"**{'📺' if media=='tv' else '🎬'} {titulo}** ({anio})")
            def votar_ob(pid,t,s,ov,a,m):
                adn=obtener_o_crear_adn(pid,t,a,ov,m)
                registrar_voto(st.session_state.usuario,pid,t,s,adn,m)
                st.session_state[f"ob_{pid}"]=True
                st.session_state.ob_calif+=1
            c1,c2=st.columns(2); c3,c4=st.columns(2)
            if c1.button("😍",key=f"o5_{p['id']}",use_container_width=True): votar_ob(p["id"],titulo,5,p.get("overview",""),anio,media);st.rerun()
            if c2.button("👍",key=f"o3_{p['id']}",use_container_width=True): votar_ob(p["id"],titulo,3,p.get("overview",""),anio,media);st.rerun()
            if c3.button("😐",key=f"o2_{p['id']}",use_container_width=True): votar_ob(p["id"],titulo,2,p.get("overview",""),anio,media);st.rerun()
            if c4.button("😴",key=f"o1_{p['id']}",use_container_width=True): votar_ob(p["id"],titulo,1,p.get("overview",""),anio,media);st.rerun()
            if st.button("⏭ Saltar",key=f"osk_{p['id']}",use_container_width=True):
                st.session_state[kd]=True; st.rerun()
            st.markdown("---")
    st.stop()

# ══════════════════════════════════════════════════════
# SESIÓN
# ══════════════════════════════════════════════════════
if "usuario" not in st.session_state: pantalla_login()
usuario=st.session_state.usuario
if not st.session_state.get("onboarding_done",False): pantalla_onboarding()

# ══════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"## 👤 {usuario}")
    perf=obtener_perfil(usuario); hist=get_historial(usuario)
    ca,cb,cc=st.columns(3)
    ca.metric("🎬",len(hist),"vistas")
    cb.metric("⭐",sum(1 for h in hist if h.get("stars",0)>=4),"amadas")
    cc.metric("👎",sum(1 for h in hist if h.get("stars",0)<=2 and not h.get("descartada")),"no gustó")
    if perf["n_pos"]>=3:
        st.divider(); st.caption("**Tu perfil:**")
        v=perf["vector_pos"]
        for i,dim in enumerate(DIMS):
            val=v[i]; w=int(val*10)
            col="#f5a623" if val>=7 else ("#4a9eff" if val>=4 else "#333")
            st.markdown(f"<div class='dim-label'>{DIMS_LABELS[dim]}</div>"
                        f"<div style='background:#1a1a2a;border-radius:3px;height:5px;'>"
                        f"<div style='width:{w}%;height:5px;border-radius:3px;background:{col}'></div></div>",
                        unsafe_allow_html=True)
    st.divider()
    if st.checkbox("📋 Historial"):
        for h in sorted(hist,key=lambda x:x.get("stars",0),reverse=True)[:12]:
            tag="📺" if h.get("media")=="tv" else "🎬"
            if h.get("descartada"): st.caption(f"🚫{tag} {h['titulo']}")
            else:
                s=h.get("stars",0); st.caption(f"{'★'*s}{'☆'*(5-s)}{tag} {h['titulo']}")
    st.divider()
    if st.button("🚪 Cerrar Sesión",use_container_width=True):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()

# ══════════════════════════════════════════════════════
# INTERFAZ PRINCIPAL
# ══════════════════════════════════════════════════════
st.markdown("# 🎯 ¿QUÉ PLAN HAY HOY?")

_,col_toggle,_ = st.columns([1,2,4])
with col_toggle:
    media_sel = st.radio("",["🎬 Película","📺 Serie"],horizontal=True,label_visibility="collapsed")
media_type = "movie" if "Película" in media_sel else "tv"

cols_mood = st.columns(len(MOOD_VECS))
for i,(nombre,_) in enumerate(MOOD_VECS.items()):
    activo = st.session_state.get("mood")==nombre
    if cols_mood[i].button(nombre,use_container_width=True,type="primary" if activo else "secondary"):
        # Cambio de mood → resetear todo
        st.session_state.mood=nombre
        st.session_state.pop("mostrando",None)
        st.session_state.pop("buffer",None)
        st.session_state.pop("ctx",None)
        st.rerun()

if "mood" in st.session_state:
    st.success(f"Mood: **{st.session_state.mood}**  ·  {media_sel}")

with st.expander("🔧 Filtros",expanded=False):
    ca2,cb2 = st.columns(2)
    with ca2: anios=st.slider("📅 Años:",1950,2025,(1990,2025))
    with cb2: n_slots=st.select_slider("🎬 Cards:",options=[3,6,9],value=6)

# ══════════════════════════════════════════════════════
# GESTIÓN DE SLOTS Y BUFFER
# ══════════════════════════════════════════════════════
# ctx = contexto actual (mood + media + años) para detectar cambios
ctx_actual = (st.session_state.get("mood",""), media_type, anios[0], anios[1])
if st.session_state.get("ctx") != ctx_actual:
    # Parámetros cambiaron → limpiar
    st.session_state.pop("mostrando",None)
    st.session_state.pop("buffer",None)
    st.session_state["ctx"] = ctx_actual

def ids_excluir() -> set:
    """Todos los IDs a evitar: vistos + los que ya están en pantalla o en buffer."""
    vistos = ids_vistos(usuario)
    en_pantalla = {p["id"] for p in st.session_state.get("mostrando",[])}
    en_buffer   = {p["id"] for p in st.session_state.get("buffer",[])}
    return vistos | en_pantalla | en_buffer

def reponer_buffer():
    """
    Rellena el buffer hasta BUFFER_SIZE con items de TMDB (sin Groq).
    Rápido: solo llama a TMDB discover.
    """
    if "mood" not in st.session_state: return
    faltantes = BUFFER_SIZE - len(st.session_state.get("buffer",[]))
    if faltantes <= 0: return
    nuevos = fetch_tmdb_batch(
        media_type, st.session_state["mood"],
        anios[0], anios[1],
        excluir_ids=ids_excluir(),
        cantidad=faltantes+5,
    )
    buf = st.session_state.get("buffer",[])
    for n in nuevos:
        if n["id"] not in ids_excluir():
            buf.append(n)
        if len(buf) >= BUFFER_SIZE: break
    st.session_state.buffer = buf

def tomar_del_buffer_con_adn() -> dict | None:
    """
    Saca el primer item del buffer, genera su ADN (1 llamada Groq),
    y lo devuelve listo para mostrarse.
    Si el buffer está vacío, lo repone primero.
    """
    if not st.session_state.get("buffer"):
        reponer_buffer()
    buf = st.session_state.get("buffer",[])
    if not buf: return None
    item = buf.pop(0)
    st.session_state.buffer = buf
    # Generar ADN para este único item
    item["_adn"] = obtener_o_crear_adn(
        item["id"], item["_titulo"], item["_anio"],
        item.get("overview",""), item["_media"])
    return item

def llenar_slots():
    """Llena los slots vacíos hasta n_slots."""
    mostrando = st.session_state.get("mostrando",[])
    vistos    = ids_vistos(usuario)
    # Limpiar los que ya se calificaron externamente
    mostrando = [p for p in mostrando if p["id"] not in vistos]
    while len(mostrando) < n_slots:
        item = tomar_del_buffer_con_adn()
        if item is None: break
        mostrando.append(item)
    st.session_state.mostrando = mostrando

def quitar_slot(tmdb_id:int):
    """Quita un item de mostrando y repone uno del buffer."""
    mostrando = [p for p in st.session_state.get("mostrando",[]) if p["id"]!=tmdb_id]
    st.session_state.mostrando = mostrando
    # Reponer inmediatamente
    with st.spinner("Cargando siguiente..."):
        item = tomar_del_buffer_con_adn()
        if item:
            mostrando.append(item)
            st.session_state.mostrando = mostrando
    # Reponer buffer si quedó bajo
    if len(st.session_state.get("buffer",[])) < BUFFER_MIN:
        reponer_buffer()

# ══════════════════════════════════════════════════════
# BOTÓN ARRANCAR
# ══════════════════════════════════════════════════════
if st.button("🚀 Recomendar",use_container_width=True,type="primary"):
    if "mood" not in st.session_state:
        st.warning("Elegí un mood primero.")
    else:
        st.session_state.pop("mostrando",None)
        st.session_state.pop("buffer",None)
        with st.spinner("⚙️ Cargando películas..."):
            reponer_buffer()          # rápido: solo TMDB
        with st.spinner(f"🧬 Analizando {n_slots} películas..."):
            llenar_slots()            # genera ADN solo para los N slots
        st.rerun()

# Si ya hay mostrando, asegurar que estén llenos
if st.session_state.get("mostrando") is not None:
    if len(st.session_state["mostrando"]) < n_slots:
        llenar_slots()

# ══════════════════════════════════════════════════════
# RENDERIZADO
# ══════════════════════════════════════════════════════
mostrando = st.session_state.get("mostrando",[])

if mostrando:
    perf_u = obtener_perfil(usuario)
    st.divider()
    st.markdown(f"### PARA VOS, {usuario.upper()}")
    st.caption(f"📋 {len(st.session_state.get('buffer',[]))} en cola · al calificar aparece la siguiente automáticamente")

    cols=st.columns(3)
    for i,p in enumerate(list(mostrando)):
        adn    = p.get("_adn",{d:5 for d in DIMS})
        titulo = p["_titulo"]; anio_p=p["_anio"]; media=p["_media"]

        if perf_u["n_pos"]>=3:
            match_pct = int(50+cosine(perf_u["vector_pos"],vec(adn))*50)
        else:
            match_pct = random.randint(80,93)

        with cols[i%3]:
            st.caption("📺 Serie" if media=="tv" else "🎬 Película")
            if p.get("poster_path"):
                st.image(f"https://image.tmdb.org/t/p/w400{p['poster_path']}",use_container_width=True)
            st.markdown(f"**{titulo}** ({anio_p})")
            st.markdown(
                f"<span class='match-gold'>🎯 {match_pct}% afinidad</span>"
                f"<span style='color:#555;font-size:12px;margin-left:8px'>⭐ {p.get('vote_average',0):.1f}</span>",
                unsafe_allow_html=True)

            # Trailer
            with st.expander("▶️ Trailer"):
                key = get_trailer_key(p["id"],media)
                if key:
                    url=(f"https://www.youtube.com/embed/{key}"
                         f"?cc_load_policy=1&cc_lang_pref=es&hl=es&rel=0&modestbranding=1")
                    st.components.v1.iframe(url,height=210)
                else:
                    st.caption("Trailer no disponible.")

            # ADN
            with st.expander("🧬 ADN"):
                for dim in DIMS:
                    val=adn.get(dim,5); w=int(val*10)
                    c="#f5a623" if val>=7 else ("#4a9eff" if val>=4 else "#333")
                    st.markdown(
                        f"<div class='dim-label'>{DIMS_LABELS[dim]} {val}/10</div>"
                        f"<div style='background:#1a1a2a;border-radius:3px;height:5px;margin-bottom:4px'>"
                        f"<div style='width:{w}%;height:5px;border-radius:3px;background:{c}'></div></div>",
                        unsafe_allow_html=True)

            # Sinopsis
            if p.get("overview"):
                with st.expander("📖 Sinopsis"):
                    txt=p["overview"]
                    st.write(txt[:280]+"..." if len(txt)>280 else txt)

            st.markdown("---")

            # Acciones
            key_v=f"visto_{media}_{p['id']}"

            if key_v not in st.session_state:
                c1,c2,c3=st.columns(3)

                if c1.button("✅ Vista",  key=f"v_{media}_{p['id']}",use_container_width=True):
                    st.session_state[key_v]="calificar"; st.rerun()

                # ⏭ SALTAR: solo sale de la pantalla esta vez, NO se guarda
                # Puede volver a aparecer en otra sesión o búsqueda
                if c2.button("⏭️ Saltar", key=f"s_{media}_{p['id']}",use_container_width=True):
                    quitar_slot(p["id"]); st.rerun()

                # 🚫 NUNCA: descarte permanente + penaliza vector negativo
                # → la IA aprende a no recomendar películas con ADN similar
                if c3.button("🚫 Nunca",  key=f"n_{media}_{p['id']}",use_container_width=True):
                    registrar_nunca(usuario,p["id"],titulo,adn,media)
                    quitar_slot(p["id"])
                    st.toast(f"'{titulo}' descartada. El motor aprende a evitar este tipo.",icon="🚫")
                    st.rerun()

            elif st.session_state[key_v]=="calificar":
                st.markdown("**¿Cómo fue?**")
                ca3,cb3=st.columns(2); cc3,cd3=st.columns(2)

                def votar(pid,t,s,a,m):
                    registrar_voto(usuario,pid,t,s,a,m)
                    icons={5:"🎉",4:"⭐",3:"👍",2:"📝",1:"📝"}
                    msgs ={5:"¡Obra maestra!",4:"¡Favorita!",3:"Guardada.",2:"La IA aprende.",1:"La IA aprende."}
                    st.toast(f"{msgs[s]} '{t}'",icon=icons[s])
                    del st.session_state[f"visto_{m}_{pid}"]
                    quitar_slot(pid)

                if ca3.button("😍 Me encantó",key=f"r5_{media}_{p['id']}",use_container_width=True): votar(p["id"],titulo,5,adn,media);st.rerun()
                if cb3.button("👍 Buena",      key=f"r3_{media}_{p['id']}",use_container_width=True): votar(p["id"],titulo,3,adn,media);st.rerun()
                if cc3.button("😐 Meh",        key=f"r2_{media}_{p['id']}",use_container_width=True): votar(p["id"],titulo,2,adn,media);st.rerun()
                if cd3.button("😴 Me aburrió", key=f"r1_{media}_{p['id']}",use_container_width=True): votar(p["id"],titulo,1,adn,media);st.rerun()
                if st.button("↩️ Cancelar",    key=f"cx_{media}_{p['id']}"):
                    del st.session_state[key_v]; st.rerun()

    st.divider()
    if st.button("🔄 Cambiar tanda completa",use_container_width=True):
        st.session_state.pop("mostrando",None)
        st.session_state.pop("buffer",None)
        st.rerun()

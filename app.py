"""SP-Metrodle — interface Streamlit."""

import json as _json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as _components
from streamlit_local_storage import LocalStorage

from game import (
    CORES_LINHAS,
    LINHAS_INFO,
    avaliar_linhas,
    avaliar_palpite,
    carregar_estacoes,
    construir_grafo,
    sortear_estacao,
)

MAX_TENTATIVAS = 6

# ── Configuração da página ─────────────────────────────────────────────────

st.set_page_config(page_title="SP-Metrodle", page_icon="🚇", layout="centered")

st.title("🚇 SP-Metrodle")
st.caption("Adivinhe a estação secreta do Metrô de São Paulo!")

# ── Dados (carregados uma vez em cache) ───────────────────────────────────

@st.cache_data
def dados():
    estacoes = carregar_estacoes()
    grafo = construir_grafo(estacoes)
    por_nome = {e["nome"]: e for e in estacoes}
    nomes = sorted(por_nome.keys())
    # Pré-computa as coordenadas de cada linha ordenadas para o mapa
    por_linha = {}
    for e in estacoes:
        for linha in e["linhas"]:
            por_linha.setdefault(linha, []).append(e)
    linhas_coords = {
        linha: [
            [e["lat"], e["lon"]]
            for e in sorted(membros, key=lambda e: e["ordem"][str(linha)])
        ]
        for linha, membros in por_linha.items()
    }
    return estacoes, grafo, por_nome, nomes, linhas_coords


_GEOM_PATH = Path(__file__).parent / "data" / "linhas_geom.json"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def _dist_ponto_segmento(plat, plon, alat, alon, blat, blon):
    cos_lat = math.cos(math.radians((plat + alat + blat) / 3))
    k = math.radians(1) * 6_371_000.0
    px = (plon - alon) * k * cos_lat
    py = (plat - alat) * k
    bx = (blon - alon) * k * cos_lat
    by = (blat - alat) * k
    ab2 = bx * bx + by * by
    if ab2 < 1e-10:
        return math.sqrt(px * px + py * py)
    t = max(0.0, min(1.0, (px * bx + py * by) / ab2))
    dx = px - t * bx
    dy = py - t * by
    return math.sqrt(dx * dx + dy * dy)


def _dist_ponto_geometria(plat, plon, segs):
    min_d = float("inf")
    for seg in segs:
        for i in range(len(seg) - 1):
            d = _dist_ponto_segmento(
                plat, plon, seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1]
            )
            min_d = min(min_d, d)
    return min_d


def _geom_valida(segs, coords_estacoes, dist_max=250, dist_pior=500):
    """True se maioria das estações ≤ dist_max m e nenhuma ultrapassa dist_pior m."""
    if not segs or not coords_estacoes:
        return False
    distancias = [_dist_ponto_geometria(lat, lon, segs) for lat, lon in coords_estacoes]
    aprovadas = sum(1 for d in distancias if d <= dist_max)
    return aprovadas > len(distancias) / 2 and max(distancias) <= dist_pior


def _segs_da_relacao(elem: dict) -> list:
    segs = []
    for m in elem.get("members", []):
        if m.get("type") != "way" or m.get("role") in ("stop", "platform"):
            continue
        pts = [[n["lat"], n["lon"]] for n in m.get("geometry", [])]
        if len(pts) >= 2:
            segs.append(pts)
    return segs


def _comprimento_segs(segs: list) -> float:
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += _haversine_m(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
    return total


@st.cache_data(ttl=604800, show_spinner="Carregando geometria das linhas...")
def buscar_geometria_osm() -> dict:
    """
    Retorna {numero_linha: [[[lat, lon], ...], ...]} — lista de segmentos por linha.

    Prioridade:
      1. data/linhas_geom.json (pré-computado, commitado no repo)
      2. Overpass ao vivo — uma requisição por linha para evitar timeout
      3. {} (app usa segmento reto entre estações como fallback)
    """
    # 1. Arquivo pré-computado
    try:
        with open(_GEOM_PATH, encoding="utf-8") as f:
            raw = _json.load(f)
        result = {}
        for k, v in raw.items():
            ref = int(k)
            if ref not in CORES_LINHAS or not v:
                continue
            # suporte ao formato antigo (lista plana de [lat,lon])
            if isinstance(v[0][0], (int, float)):
                result[ref] = [v]
            else:
                result[ref] = v
        return result
    except Exception:
        pass

    # 2. Overpass ao vivo — uma requisição por linha para evitar timeout
    def _fetch_ref(ref: int) -> list:
        query = (
            f"[out:json][timeout:60];"
            f"area[\"name\"=\"São Paulo\"][\"admin_level\"=\"8\"]->.a;"
            f"relation[\"route\"~\"subway|monorail\"][\"ref\"=\"{ref}\"](area.a);"
            f"out geom;"
        )
        url = ("https://overpass-api.de/api/interpreter?"
               + urllib.parse.urlencode({"data": query}))
        req = urllib.request.Request(url, headers={"User-Agent": "metroquiz/1.0"})
        with urllib.request.urlopen(req, timeout=50) as resp:
            return _json.loads(resp.read()).get("elements", [])

    import time as _time
    linhas: dict = {}
    for ref in sorted(CORES_LINHAS.keys()):
        for _tentativa in range(3):
            try:
                elems = _fetch_ref(ref)
                break
            except Exception:
                _time.sleep((_tentativa + 1) * 6)
        else:
            continue
        por_ref_elems = [e for e in elems if e.get("type") == "relation"]
        if not por_ref_elems:
            continue
        melhor = max(por_ref_elems, key=lambda e: _comprimento_segs(_segs_da_relacao(e)))
        segs = _segs_da_relacao(melhor)
        if segs:
            linhas[ref] = segs
        _time.sleep(2)
    return linhas


estacoes, grafo, por_nome, nomes, linhas_coords = dados()

# ── LocalStorage ──────────────────────────────────────────────────────────

_ls       = LocalStorage()
_LS_CHAVE = "metroquiz_estado"


def _salvar_estado() -> None:
    _ls.setItem(_LS_CHAVE, {
        "secreta_nome":      st.session_state.secreta["nome"],
        "palpites":          st.session_state.palpites,
        "fim":               st.session_state.fim,
        "vitoria":           st.session_state.vitoria,
        "rodadas":           st.session_state.rodadas,
        "vitorias":          st.session_state.vitorias,
        "streak":            st.session_state.streak,
        "tentativas_total":  st.session_state.tentativas_total,
        "modo_dificil":      st.session_state.get("modo_dificil", False),
    })


# ── Estado da sessão ──────────────────────────────────────────────────────

def nova_rodada():
    st.session_state.secreta = sortear_estacao(estacoes)
    st.session_state.palpites = []
    st.session_state.fim = False
    st.session_state.vitoria = False


if "secreta" not in st.session_state:
    # render 1 → getItem retorna None (componente ainda carregando); render 2 → valor real
    if "_ls_render" not in st.session_state:
        st.session_state._ls_render = 0
    _blob = _ls.getItem(_LS_CHAVE)
    st.session_state._ls_render += 1

    if _blob is None and st.session_state._ls_render == 1:
        st.rerun()   # aguarda render 2 para ler o localStorage com valor real

    _restaurado = False
    if isinstance(_blob, dict):
        _nome = _blob.get("secreta_nome")
        if _nome and _nome in por_nome:
            try:
                st.session_state.secreta          = por_nome[_nome]
                st.session_state.palpites         = _blob.get("palpites", [])
                st.session_state.fim              = bool(_blob.get("fim"))
                st.session_state.vitoria          = bool(_blob.get("vitoria"))
                st.session_state.rodadas          = int(_blob.get("rodadas", 0))
                st.session_state.vitorias         = int(_blob.get("vitorias", 0))
                st.session_state.streak           = int(_blob.get("streak", 0))
                st.session_state.tentativas_total = int(_blob.get("tentativas_total", 0))
                st.session_state.modo_dificil     = bool(_blob.get("modo_dificil", False))
                _restaurado = True
            except Exception:
                pass

    if not _restaurado:
        nova_rodada()
        st.session_state.rodadas          = 0
        st.session_state.vitorias         = 0
        st.session_state.streak           = 0
        st.session_state.tentativas_total = 0
        st.session_state.modo_dificil     = False


# ── Placar de sessão ──────────────────────────────────────────────────────

with st.sidebar:
    st.header("📊 Placar da sessão")
    rodadas  = st.session_state.rodadas
    vitorias = st.session_state.vitorias
    streak   = st.session_state.streak
    media    = (
        st.session_state.tentativas_total / vitorias
        if vitorias > 0 else 0
    )

    col1, col2 = st.columns(2)
    col1.metric("Rodadas",  rodadas)
    col2.metric("Vitórias", vitorias)
    col1.metric("Streak 🔥", streak)
    col2.metric("Média tent.", f"{media:.1f}" if vitorias else "-")

    st.divider()
    st.markdown("**Instruções**")
    st.markdown(
        "- Digite o nome de uma estação e aperte Enter.\n"
        "- O jogo mostra: chips de linha, distância em estações e a direção.\n"
        "- Chips **sem ✗** = linha em comum com a secreta.\n"
        "- Você tem **6 tentativas** por rodada.\n"
        "- Clique **Nova estação** para jogar de novo.\n\n"
        "_O mapa precisa de conexão à internet._"
    )

    st.divider()
    st.session_state.modo_dificil = st.toggle(
        "🎯 Modo Difícil",
        value=st.session_state.get("modo_dificil", False),
    )
    if st.session_state.modo_dificil:
        st.caption("Linhas ocultas no início. Uma nova linha revelada a cada erro.")


# ── Template MapLibre GL ──────────────────────────────────────────────────

_HTML_MAPA = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link href="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
#map{width:100%;height:420px}
</style>
</head>
<body>
<div id="map"></div>
<script>
/*DADOS*/
const map = new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/liberty',
  center:CENTER,
  zoom:16,
  pitch:0,
  bearing:0,
  maxPitch:0,
  interactive:false,
  maxBounds:[[CENTER[0]-DELTA,CENTER[1]-DELTA],[CENTER[0]+DELTA,CENTER[1]+DELTA]]
});
map.on('load',function(){
  map.setPitch(0);
  map.setBearing(0);
  for(const layer of map.getStyle().layers){
    if(layer.type==='symbol') map.setLayoutProperty(layer.id,'visibility','none');
    if(layer.type==='fill-extrusion'){
      map.setPaintProperty(layer.id,'fill-extrusion-height',0);
      map.setPaintProperty(layer.id,'fill-extrusion-base',0);
    }
  }
  for(const [linha,segs] of Object.entries(GEOM)){
    map.addSource('l'+linha,{type:'geojson',data:{type:'FeatureCollection',features:segs.map(c=>({type:'Feature',geometry:{type:'LineString',coordinates:c}}))}});
    map.addLayer({id:'l'+linha,type:'line',source:'l'+linha,paint:{'line-color':'#777777','line-width':3,'line-opacity':0.8}});
  }
  for(const g of GUESSES){
    const el=document.createElement('div');
    el.style.cssText='width:16px;height:16px;border-radius:50%;background:'+g.cor+';border:2px solid white;';
    new maplibregl.Marker({element:el,anchor:'center'}).setLngLat([g.lon,g.lat]).addTo(map);
  }
  const tel=document.createElement('div');
  if(TARGET.revelado){
    tel.style.cssText='width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:22px;filter:drop-shadow(0 0 2px #000);';
    tel.textContent='⭐';
  } else {
    tel.style.cssText='width:20px;height:20px;border-radius:50%;background:white;border:3px solid #222;';
  }
  new maplibregl.Marker({element:tel,anchor:'center'}).setLngLat([TARGET.lon,TARGET.lat]).addTo(map);
  if(TARGET.revelado){
    new maplibregl.Popup({closeButton:false,closeOnClick:false,offset:20})
      .setHTML('<strong>'+TARGET.nome+'</strong>')
      .setLngLat([TARGET.lon,TARGET.lat])
      .addTo(map);
  }
});
</script>
</body>
</html>"""


# ── Funções de renderização ───────────────────────────────────────────────

def chip_linha(linha: int, cor: str, bate: bool) -> str:
    """Chip colorido de linha; se não bate com a secreta, exibe ✗ vermelho."""
    if bate:
        return (
            f'<span style="background:{cor};color:white;padding:3px 9px;'
            f'border-radius:10px;font-weight:bold;font-size:0.85em;margin-right:4px">'
            f'L{linha}</span>'
        )
    return (
        f'<span style="display:inline-block;margin-right:4px">'
        f'<span style="background:{cor};color:white;padding:3px 9px;'
        f'border-radius:10px;font-weight:bold;font-size:0.85em;opacity:0.7">'
        f'L{linha}</span>'
        f'<span style="color:#cc0000;font-weight:900"> ✗</span>'
        f'</span>'
    )


def renderizar_mapa():
    """Mapa MapLibre GL (OpenFreeMap) travado nos arredores da secreta."""
    secreta      = st.session_state.secreta
    modo_dificil = st.session_state.get("modo_dificil", False)
    num_erros    = sum(1 for _, res in st.session_state.palpites if not res["acertou"])

    ordem_linhas = list(CORES_LINHAS.keys())
    linhas_visiveis = (
        set(ordem_linhas[:num_erros]) if modo_dificil else set(CORES_LINHAS.keys())
    )

    geom_osm = buscar_geometria_osm()

    # Monta geometria para MapLibre — converte [lat, lon] → [lon, lat]
    geom_js: dict = {}
    for linha in linhas_visiveis:
        segs_linha = geom_osm.get(linha)
        coords_est = linhas_coords.get(linha, [])
        if segs_linha and _geom_valida(segs_linha, coords_est):
            segs = segs_linha
        elif coords_est:
            segs = [coords_est]
        else:
            continue
        geom_js[str(linha)] = [
            [[pt[1], pt[0]] for pt in seg]
            for seg in segs
        ]

    lat, lon = secreta["lat"], secreta["lon"]

    guesses_data = [
        {"lon": por_nome[nome]["lon"], "lat": por_nome[nome]["lat"],
         "cor": CORES_LINHAS.get(por_nome[nome]["linhas"][0], "#888888"), "nome": nome}
        for nome, _ in st.session_state.palpites
    ]
    target_data = {
        "lon": lon, "lat": lat,
        "revelado": st.session_state.fim,
        "nome": secreta["nome"],
    }

    dados = (
        f"const CENTER=[{lon},{lat}];\n"
        f"const DELTA=0.008;\n"
        f"const GEOM={_json.dumps(geom_js)};\n"
        f"const GUESSES={_json.dumps(guesses_data)};\n"
        f"const TARGET={_json.dumps(target_data)};\n"
    )
    html = _HTML_MAPA.replace("/*DADOS*/", dados)
    _components.html(html, height=422)


def renderizar_historico():
    palpites = st.session_state.palpites
    if not palpites:
        return

    secreta = st.session_state.secreta
    st.subheader("Histórico")
    for i, (nome, res) in enumerate(palpites, 1):
        chips = avaliar_linhas(por_nome[nome], secreta)
        dist  = res["distancia"]
        seta  = res["direcao"]

        badges = "".join(chip_linha(c["linha"], c["cor"], c["bate"]) for c in chips)
        emoji_dist = "🟢" if dist == 0 else ("🟡" if dist <= 3 else "🔴")
        label_dist = f"{dist} estação" if dist == 1 else f"{dist} estações"

        col_n, col_l, col_d, col_dir = st.columns([3, 2, 2, 1])
        col_n.markdown(f"**{i}.** {nome}")
        col_l.markdown(badges, unsafe_allow_html=True)
        col_d.markdown(f"{emoji_dist} 🚉 {label_dist}")
        col_dir.markdown(f"<h2 style='margin:0'>{seta}</h2>", unsafe_allow_html=True)


def renderizar_legenda_linhas():
    st.markdown("**Linhas do Metrô SP:**", unsafe_allow_html=True)
    partes = []
    for num, info in LINHAS_INFO.items():
        cor  = info["cor"]
        nome = info["nome"]
        partes.append(
            f'<span style="background:{cor};color:white;'
            f'padding:3px 8px;border-radius:12px;font-weight:bold;'
            f'font-size:0.8em">L{num} {nome}</span>'
        )
    st.markdown(" ".join(partes), unsafe_allow_html=True)


# ── Área principal ────────────────────────────────────────────────────────

renderizar_mapa()
renderizar_historico()

if not st.session_state.fim:
    tentativas_feitas = len(st.session_state.palpites)
    st.info(f"Tentativa {tentativas_feitas + 1} de {MAX_TENTATIVAS}")

    escolha = st.selectbox(
        "Digite ou selecione uma estação:",
        options=[""] + nomes,
        index=0,
        key="input_estacao",
    )

    if st.button("Palpitar", type="primary", disabled=(not escolha)):
        if escolha in por_nome:
            tentadas = [p[0] for p in st.session_state.palpites]
            if escolha in tentadas:
                st.warning("Você já tentou essa estação!")
            else:
                palpite_dict = por_nome[escolha]
                resultado = avaliar_palpite(
                    palpite_dict,
                    st.session_state.secreta,
                    grafo,
                )
                st.session_state.palpites.append((escolha, resultado))

                if resultado["acertou"]:
                    st.session_state.fim = True
                    st.session_state.vitoria = True
                    st.session_state.rodadas += 1
                    st.session_state.vitorias += 1
                    st.session_state.streak += 1
                    st.session_state.tentativas_total += len(st.session_state.palpites)
                elif len(st.session_state.palpites) >= MAX_TENTATIVAS:
                    st.session_state.fim = True
                    st.session_state.vitoria = False
                    st.session_state.rodadas += 1
                    st.session_state.streak = 0

                _salvar_estado()
                st.rerun()
        else:
            st.error("Estação não encontrada. Tente novamente.")

if st.session_state.fim:
    secreta = st.session_state.secreta
    tentativas = len(st.session_state.palpites)

    if st.session_state.vitoria:
        st.success(
            f"🎯 Você acertou em {tentativas} tentativa{'s' if tentativas != 1 else ''}! "
            f"A estação era **{secreta['nome']}**."
        )
    else:
        linhas_str = ", ".join(
            f"L{l} {LINHAS_INFO[l]['nome']}" for l in secreta["linhas"]
        )
        st.error(
            f"❌ Suas tentativas acabaram! A estação era **{secreta['nome']}** ({linhas_str})."
        )

    if st.button("🔄 Nova estação", type="primary"):
        nova_rodada()
        _salvar_estado()
        st.rerun()

st.divider()
renderizar_legenda_linhas()

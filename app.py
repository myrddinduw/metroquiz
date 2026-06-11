"""SP-Metrodle — interface Streamlit."""

import json as _json
import urllib.parse
import urllib.request

import folium
import streamlit as st
from folium import MacroElement
from jinja2 import Template
from streamlit_folium import st_folium
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


@st.cache_data(ttl=604800, show_spinner="Carregando geometria das linhas (OpenStreetMap)...")
def buscar_geometria_osm() -> dict:
    """
    Busca geometria real das linhas do Metrô SP via Overpass API.
    Retorna {numero_linha: [[lat, lon], ...]}; dicionário vazio se falhar.
    Resultado cacheado por 7 dias para não travar o app a cada acesso.
    """
    query = (
        "[out:json][timeout:20];"
        "(relation[\"network\"=\"Metrô SP\"][\"type\"=\"route\"][\"route\"~\"subway|monorail\"];"
        "relation[\"network\"=\"ViaQuatro\"][\"type\"=\"route\"][\"route\"=\"subway\"];"
        "relation[\"network\"=\"ViaMobilidade\"][\"type\"=\"route\"][\"route\"~\"subway|monorail\"];);"
        "out geom;"
    )
    try:
        payload = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resultado = _json.loads(resp.read())
    except Exception:
        return {}

    linhas: dict = {}
    for elem in resultado.get("elements", []):
        if elem["type"] != "relation":
            continue
        try:
            ref = int(elem.get("tags", {}).get("ref", ""))
        except ValueError:
            continue
        # Aceita só linhas conhecidas; ignora variantes (já processadas)
        if ref not in CORES_LINHAS or ref in linhas:
            continue
        coords: list = []
        for membro in elem.get("members", []):
            if membro.get("type") != "way" or membro.get("role") in ("stop", "platform"):
                continue
            for node in membro.get("geometry", []):
                pt = [node["lat"], node["lon"]]
                if not coords or coords[-1] != pt:
                    coords.append(pt)
        if coords:
            linhas[ref] = coords
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


# ── Componentes Folium ────────────────────────────────────────────────────

class TravarPan(MacroElement):
    """Injeta JS para desabilitar toda interação de pan/zoom do mapa Leaflet."""
    def __init__(self):
        super().__init__()
        self._template = Template(
            "{% macro script(this, kwargs) %}"
            "{{ this._parent.get_name() }}.dragging.disable();"
            "{{ this._parent.get_name() }}.touchZoom.disable();"
            "{{ this._parent.get_name() }}.doubleClickZoom.disable();"
            "{{ this._parent.get_name() }}.scrollWheelZoom.disable();"
            "{{ this._parent.get_name() }}.keyboard.disable();"
            "{% endmacro %}"
        )


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
    """Mapa Folium travado nos arredores da secreta, linhas em cor neutra."""
    secreta      = st.session_state.secreta
    modo_dificil = st.session_state.get("modo_dificil", False)
    num_erros    = sum(1 for _, res in st.session_state.palpites if not res["acertou"])

    # Tiles: MapTiler (se chave configurada) ou CartoDB sem rótulos (gratuito)
    try:
        chave = st.secrets["maptiler_key"]
        tiles_url  = f"https://api.maptiler.com/maps/positron/{{z}}/{{x}}/{{y}}.png?key={chave}"
        tiles_attr = "© MapTiler © OpenStreetMap contributors"
    except (KeyError, FileNotFoundError, AttributeError):
        tiles_url  = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png"
        tiles_attr = "© OpenStreetMap contributors © CARTO"

    # Caixa de confinamento: ±0.008° ao redor da secreta (~900 m)
    delta = 0.008
    lat, lon = secreta["lat"], secreta["lon"]

    m = folium.Map(
        location=[lat, lon],
        zoom_start=15,
        min_zoom=15,
        max_zoom=15,
        max_bounds=True,
        min_lat=lat - delta,
        max_lat=lat + delta,
        min_lon=lon - delta,
        max_lon=lon + delta,
        tiles=tiles_url,
        attr=tiles_attr,
        zoom_control=False,
    )
    # Desabilita todas as interações de pan/zoom via JavaScript
    TravarPan().add_to(m)

    # Em modo difícil, revela uma linha por erro na ordem [1,2,3,4,5,15]
    ordem_linhas = list(CORES_LINHAS.keys())
    linhas_visiveis = (
        set(ordem_linhas[:num_erros]) if modo_dificil else set(CORES_LINHAS.keys())
    )

    # Geometria real via OSM; se indisponível, usa segmentos retos entre estações
    geom_osm = buscar_geometria_osm()

    # Cor neutra única: a cor por linha é dica exclusiva dos chips de feedback
    for linha in linhas_visiveis:
        coords = geom_osm.get(linha) or linhas_coords.get(linha, [])
        if coords:
            folium.PolyLine(
                coords,
                color="#777777",
                weight=3,
                opacity=0.8,
            ).add_to(m)

    # Marcadores dos palpites
    for nome, _ in st.session_state.palpites:
        e = por_nome[nome]
        cor_p = CORES_LINHAS.get(e["linhas"][0], "#888888")
        folium.CircleMarker(
            location=[e["lat"], e["lon"]],
            radius=8,
            color="white",
            weight=2,
            fill=True,
            fill_color=cor_p,
            fill_opacity=0.9,
            tooltip=nome,
        ).add_to(m)

    # Alvo da secreta — sempre visível, sem texto (círculo branco com borda escura)
    folium.CircleMarker(
        location=[lat, lon],
        radius=10,
        color="#222222",
        weight=3,
        fill=True,
        fill_color="white",
        fill_opacity=0.95,
    ).add_to(m)

    # Ao fim revela o nome sobrepondo a estrela (DivIcon = sem imagem externa)
    if st.session_state.fim:
        folium.Marker(
            location=[lat, lon],
            tooltip=secreta["nome"],
            icon=folium.DivIcon(
                html='<div style="font-size:26px;line-height:1;filter:drop-shadow(0 0 2px #000)">⭐</div>',
                icon_size=(30, 30),
                icon_anchor=(15, 15),
            ),
        ).add_to(m)

    st_folium(m, width="100%", height=420, returned_objects=[], key="mapa_principal")


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

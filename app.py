"""SP-Metrodle — interface Streamlit."""

import streamlit as st
from game import (
    LINHAS_INFO,
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
    return estacoes, grafo, por_nome, nomes

estacoes, grafo, por_nome, nomes = dados()

# ── Estado da sessão ──────────────────────────────────────────────────────

def nova_rodada():
    st.session_state.secreta = sortear_estacao(estacoes)
    st.session_state.palpites = []
    st.session_state.fim = False
    st.session_state.vitoria = False

if "secreta" not in st.session_state:
    nova_rodada()
    st.session_state.rodadas = 0
    st.session_state.vitorias = 0
    st.session_state.streak = 0
    st.session_state.tentativas_total = 0


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
        "- O jogo mostra: linhas em comum, distância em saltos e a direção.\n"
        "- Você tem **6 tentativas** por rodada.\n"
        "- Clique **Nova estação** para jogar de novo."
    )


# ── Área principal ────────────────────────────────────────────────────────

def cor_linha(numero: int) -> str:
    return LINHAS_INFO.get(numero, {}).get("cor", "#888888")


def badge_linha(numero: int) -> str:
    """Retorna um span HTML colorido com o número da linha."""
    cor = cor_linha(numero)
    return (
        f'<span style="background:{cor};color:white;'
        f'padding:2px 7px;border-radius:10px;font-weight:bold;'
        f'font-size:0.85em;margin-right:3px">L{numero}</span>'
    )


def renderizar_historico():
    palpites = st.session_state.palpites
    if not palpites:
        return

    st.subheader("Histórico")
    for i, (nome, res) in enumerate(palpites, 1):
        comuns = res["linhas_comuns"]
        dist   = res["distancia"]
        seta   = res["direcao"]

        badges = "".join(badge_linha(l) for l in comuns) if comuns else "—"
        emoji_dist = "🟢" if dist == 0 else ("🟡" if dist <= 3 else "🔴")

        col_n, col_l, col_d, col_dir = st.columns([3, 2, 2, 1])
        col_n.markdown(f"**{i}.** {nome}")
        col_l.markdown(badges, unsafe_allow_html=True)
        col_d.markdown(f"{emoji_dist} {dist} salto{'s' if dist != 1 else ''}")
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


# Histórico de palpites
renderizar_historico()

# Formulário de entrada (só exibe se o jogo ainda não acabou)
if not st.session_state.fim:
    tentativas_feitas = len(st.session_state.palpites)
    restantes = MAX_TENTATIVAS - tentativas_feitas
    st.info(f"Tentativa {tentativas_feitas + 1} de {MAX_TENTATIVAS}")

    escolha = st.selectbox(
        "Digite ou selecione uma estação:",
        options=[""] + nomes,
        index=0,
        key="input_estacao",
    )

    if st.button("Palpitar", type="primary", disabled=(not escolha)):
        if escolha in por_nome:
            # Verifica se já foi tentado
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

                st.rerun()
        else:
            st.error("Estação não encontrada. Tente novamente.")

# Mensagem de fim de rodada
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
        st.rerun()

st.divider()
renderizar_legenda_linhas()

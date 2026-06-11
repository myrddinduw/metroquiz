"""Lógica do SP-Metrodle: carregamento, grafo, BFS, direção e sorteio."""

import json
import math
import random
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

# Cores oficiais das linhas (hex) — fonte única de verdade
CORES_LINHAS: Dict[int, str] = {
    1:  "#0455A1",
    2:  "#007E5E",
    3:  "#EE372F",
    4:  "#FFD400",
    5:  "#92278F",
    15: "#9C9C9C",
    17: "#C9A94A",
}

# Mapeamento de número de linha → nome e cor hex
LINHAS_INFO: Dict[int, Dict] = {
    num: {"nome": nome, "cor": CORES_LINHAS[num]}
    for num, nome in {
        1: "Azul", 2: "Verde", 3: "Vermelha",
        4: "Amarela", 5: "Lilás", 15: "Prata", 17: "Ouro",
    }.items()
}


def carregar_estacoes() -> List[Dict]:
    """Lê data/estacoes.json e retorna lista de dicionários de estações."""
    caminho = Path(__file__).parent / "data" / "estacoes.json"
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def construir_grafo(estacoes: List[Dict]) -> Dict[str, List[str]]:
    """
    Constrói grafo de adjacência entre estações.

    Duas estações são vizinhas se forem consecutivas (diferença de ordem = 1)
    na mesma linha. Baldeações conectam automaticamente as linhas via BFS
    porque a mesma estação aparece em várias linhas.
    """
    por_nome = {e["nome"]: e for e in estacoes}

    por_linha: Dict[int, List[Dict]] = {}
    for e in estacoes:
        for linha in e["linhas"]:
            por_linha.setdefault(linha, []).append(e)

    grafo: Dict[str, List[str]] = {e["nome"]: [] for e in estacoes}

    for linha, membros in por_linha.items():
        membros_ord = sorted(membros, key=lambda e: e["ordem"][str(linha)])
        for i in range(len(membros_ord) - 1):
            a = membros_ord[i]["nome"]
            b = membros_ord[i + 1]["nome"]
            if b not in grafo[a]:
                grafo[a].append(b)
            if a not in grafo[b]:
                grafo[b].append(a)

    return grafo


def distancia(grafo: Dict[str, List[str]], origem: str, destino: str) -> int:
    """
    Retorna a menor quantidade de estações entre origem e destino via BFS.
    Retorna -1 se não houver caminho (dataset inconsistente).
    """
    if origem == destino:
        return 0

    visitado = {origem}
    fila: deque = deque([(origem, 0)])

    while fila:
        atual, dist = fila.popleft()
        for vizinho in grafo[atual]:
            if vizinho == destino:
                return dist + 1
            if vizinho not in visitado:
                visitado.add(vizinho)
                fila.append((vizinho, dist + 1))

    return -1


def direcao(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> str:
    """
    Retorna seta cardinal/diagonal apontando de A para B.
    Usa limiares de 22,5° para decidir entre cardinal e diagonal.
    """
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a

    angulo = math.degrees(math.atan2(dlon, dlat)) % 360

    if angulo < 22.5 or angulo >= 337.5:
        return "↑"
    elif angulo < 67.5:
        return "↗"
    elif angulo < 112.5:
        return "→"
    elif angulo < 157.5:
        return "↘"
    elif angulo < 202.5:
        return "↓"
    elif angulo < 247.5:
        return "↙"
    elif angulo < 292.5:
        return "←"
    else:
        return "↖"


def sortear_estacao(estacoes: List[Dict]) -> Dict:
    """Retorna uma estação aleatória da lista."""
    return random.choice(estacoes)


def avaliar_linhas(palpite: Dict, secreta: Dict) -> List[Dict]:
    """
    Retorna chip de cada linha da estação palpitada indicando se bate com a secreta.
    Cada item: {linha: int, cor: str, bate: bool}.
    """
    linhas_secreta = set(secreta["linhas"])
    return [
        {
            "linha": linha,
            "cor": CORES_LINHAS.get(linha, "#888888"),
            "bate": linha in linhas_secreta,
        }
        for linha in sorted(palpite["linhas"])
    ]


def linhas_compartilhadas(palpite: Dict, secreta: Dict) -> List[int]:
    """Retorna as linhas em comum entre palpite e estação secreta."""
    return sorted(set(palpite["linhas"]) & set(secreta["linhas"]))


def avaliar_palpite(
    palpite: Dict,
    secreta: Dict,
    grafo: Dict[str, List[str]],
) -> Dict:
    """
    Avalia um palpite e retorna um dicionário com:
    - acertou: bool
    - linhas_comuns: lista de números de linha compartilhados
    - distancia: int (estações entre palpite e secreta)
    - direcao: str (seta cardinal/diagonal de palpite → secreta)
    """
    acertou = palpite["nome"] == secreta["nome"]
    comuns = linhas_compartilhadas(palpite, secreta)
    dist = distancia(grafo, palpite["nome"], secreta["nome"])
    seta = direcao(
        palpite["lat"], palpite["lon"],
        secreta["lat"], secreta["lon"],
    ) if not acertou else "🎯"

    return {
        "acertou": acertou,
        "linhas_comuns": comuns,
        "distancia": dist,
        "direcao": seta,
    }
"""Lógica do SP-Metrodle: carregamento, grafo, BFS, direção e sorteio."""

import json
import math
import random
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

# Mapeamento de número de linha → nome e cor hex
LINHAS_INFO: Dict[int, Dict] = {
    1:  {"nome": "Azul",    "cor": "#0455A4"},
    2:  {"nome": "Verde",   "cor": "#007E5E"},
    3:  {"nome": "Vermelha","cor": "#EE372F"},
    4:  {"nome": "Amarela", "cor": "#FFC20E"},
    5:  {"nome": "Lilás",   "cor": "#9B2990"},
    15: {"nome": "Prata",   "cor": "#9E9E9E"},
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
    # Índice: nome → dados da estação
    por_nome = {e["nome"]: e for e in estacoes}

    # Agrupa estações por linha com sua posição de ordem
    por_linha: Dict[int, List[Dict]] = {}
    for e in estacoes:
        for linha in e["linhas"]:
            por_linha.setdefault(linha, []).append(e)

    grafo: Dict[str, List[str]] = {e["nome"]: [] for e in estacoes}

    for linha, membros in por_linha.items():
        # Ordena pelo campo "ordem" da linha atual
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
    Retorna a menor quantidade de saltos (estações) entre origem e destino via BFS.
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
    # dlat positivo = B está ao Norte de A (lat menos negativa)
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a

    # atan2(dE, dN): 0° = Norte, 90° = Leste, 180° = Sul, 270° = Oeste
    angulo = math.degrees(math.atan2(dlon, dlat)) % 360
    angulo = angulo % 360

    # Divide 360° em 8 octantes de 45°, centrados nos pontos cardinais/diagonais
    if angulo < 22.5 or angulo >= 337.5:
        return "↑"   # Norte
    elif angulo < 67.5:
        return "↗"   # Nordeste
    elif angulo < 112.5:
        return "→"   # Leste
    elif angulo < 157.5:
        return "↘"   # Sudeste
    elif angulo < 202.5:
        return "↓"   # Sul
    elif angulo < 247.5:
        return "↙"   # Sudoeste
    elif angulo < 292.5:
        return "←"   # Oeste
    else:
        return "↖"   # Noroeste


def sortear_estacao(estacoes: List[Dict]) -> Dict:
    """Retorna uma estação aleatória da lista."""
    return random.choice(estacoes)


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
    - distancia: int (saltos entre palpite e secreta)
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

#!/usr/bin/env python3
"""
Traça cada linha do Metrô SP pelos trilhos físicos do OSM (railway=subway/monorail)
usando Dijkstra por par de estações consecutivas.

Fixes aplicados:
  1. MAX_RATIO=2.5 — rejeita caminho Dijkstra > 2.5× a distância direta.
  2. NODE_PREC=4 + costura de componentes — conecta nós desconexos < 20 m.
  3. SNAP_MAX_M=400 — ampliar alcance do snap (seguro com Fix 1 ativo).
  4. Chaikin nos gaps — trechos sem OSM recebem suavização, não retas.

Uso:
    python scripts/baixar_geometria.py

Salva data/linhas_geom.json.
"""

import heapq
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

LINHAS_ALVO      = {1, 2, 3, 4, 5, 15, 17}
SAIDA            = Path(__file__).parent.parent / "data" / "linhas_geom.json"
ESTACOES_PATH    = Path(__file__).parent.parent / "data" / "estacoes.json"

SNAP_MAX_M       = 400   # Fix 3: era 300 m
NODE_PREC        = 4     # Fix 2: funde nós a ~11 m (era 5 = ~1 m)
GAP_COSTURA_M    = 20    # Fix 2: conecta componentes desconexas até este gap
MAX_RATIO        = 2.5   # Fix 1: rejeita caminho se > X × distância direta
CHAIKIN_FALLBACK = 2     # Fix 4: iterações Chaikin nos trechos sem OSM
BBOX             = "-24.1,-46.9,-23.3,-46.3"


# ── Geometria ─────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def dist_ponto_segmento(plat, plon, alat, alon, blat, blon):
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
    return math.sqrt((px - t * bx) ** 2 + (py - t * by) ** 2)


def dist_ponto_geometria(plat, plon, segs):
    min_d = float("inf")
    for seg in segs:
        for i in range(len(seg) - 1):
            d = dist_ponto_segmento(
                plat, plon, seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1]
            )
            if d < min_d:
                min_d = d
    return min_d


def comprimento_total(segs):
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += haversine_m(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
    return total


def _comprimento_pts(pts):
    return sum(haversine_m(pts[j][0], pts[j][1], pts[j + 1][0], pts[j + 1][1])
               for j in range(len(pts) - 1))


def _mediana(vals):
    if not vals:
        return float("inf")
    s = sorted(vals)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2 if n % 2 == 0 else s[n // 2]


def pontuar(segs, coords_est):
    if not segs or not coords_est:
        return float("inf"), float("inf")
    dists = [dist_ponto_geometria(lat, lon, segs) for lat, lon in coords_est]
    return _mediana(dists), max(dists)


# ── Chaikin (Fix 4) ───────────────────────────────────────────────────────────

def chaikin(pts, iterations=CHAIKIN_FALLBACK):
    """Corner-cutting de Chaikin preservando pontos extremos."""
    for _ in range(iterations):
        novo = [pts[0]]
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            novo.append([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]])
            novo.append([0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]])
        novo.append(pts[-1])
        pts = novo
    return pts


# ── Grafo ─────────────────────────────────────────────────────────────────────

def construir_grafo(ways):
    """
    Grafo undirected de ways [[lat,lon],...].
    NODE_PREC=4 funde nós a ~11 m.
    """
    nodes: dict = {}
    adj: dict   = {}

    for way in ways:
        prev_k = None
        for lat, lon in way:
            k = (round(lat, NODE_PREC), round(lon, NODE_PREC))
            if k not in nodes:
                nodes[k] = (lat, lon)
                adj[k] = []
            if prev_k is not None and prev_k != k:
                d = haversine_m(nodes[prev_k][0], nodes[prev_k][1], lat, lon)
                adj[prev_k].append((k, d))
                adj[k].append((prev_k, d))
            prev_k = k

    return nodes, adj


def costurar_componentes(nodes, adj):
    """
    Fix 2: adiciona arestas entre nós de componentes diferentes a < GAP_COSTURA_M m.
    Modifica adj in-place. Retorna número de arestas adicionadas.
    """
    keys = list(nodes.keys())
    vizinhos = {k: {v for v, _ in adj[k]} for k in keys}
    adicionadas = 0

    for i in range(len(keys)):
        ka = keys[i]
        lat_a, lon_a = nodes[ka]
        for j in range(i + 1, len(keys)):
            kb = keys[j]
            if kb in vizinhos[ka]:
                continue
            lat_b, lon_b = nodes[kb]
            # Filtro rápido por diferença de latitude antes do haversine
            if abs(lat_b - lat_a) * 111_000 > GAP_COSTURA_M:
                continue
            d = haversine_m(lat_a, lon_a, lat_b, lon_b)
            if d < GAP_COSTURA_M:
                adj[ka].append((kb, d))
                adj[kb].append((ka, d))
                vizinhos[ka].add(kb)
                vizinhos[kb].add(ka)
                adicionadas += 1

    return adicionadas


def snap_estacao(lat, lon, nodes):
    """(key, dist_m) do nó mais próximo da estação."""
    best_k = None
    best_d = float("inf")
    for k, (nlat, nlon) in nodes.items():
        d = haversine_m(lat, lon, nlat, nlon)
        if d < best_d:
            best_d = d
            best_k = k
    return best_k, best_d


def dijkstra_caminho(adj, nodes, origem, destino):
    """Menor caminho origem→destino. Retorna [[lat,lon]] ou None."""
    heap = [(0.0, origem)]
    dist = {origem: 0.0}
    prev = {origem: None}

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if u == destino:
            break
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if destino not in prev:
        return None

    path = []
    cur = destino
    while cur is not None:
        path.append(list(nodes[cur]))
        cur = prev[cur]
    path.reverse()
    return path


# ── Estações ──────────────────────────────────────────────────────────────────

def carregar_estacoes_por_linha():
    with open(ESTACOES_PATH, encoding="utf-8") as f:
        todas = json.load(f)
    por_linha: dict = {}
    for e in todas:
        for linha in e["linhas"]:
            por_linha.setdefault(linha, []).append(e)
    result = {}
    for ref, ests in por_linha.items():
        if ref in LINHAS_ALVO:
            ord_ests = sorted(ests, key=lambda e: e["ordem"][str(ref)])
            result[ref] = [(e["lat"], e["lon"]) for e in ord_ests]
    return result


# ── OSM ───────────────────────────────────────────────────────────────────────

def buscar_trilhos():
    """Retorna todos os ways railway=subway|monorail no bbox de SP, com tags."""
    query = (
        f"[out:json][timeout:90][bbox:{BBOX}];"
        f'(way["railway"="subway"];way["railway"="monorail"];);'
        f"out geom tags;"
    )
    url = ("https://overpass-api.de/api/interpreter?"
           + urllib.parse.urlencode({"data": query}))
    for tentativa in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "metroquiz/1.0"})
            with urllib.request.urlopen(req, timeout=95) as resp:
                return json.loads(resp.read()).get("elements", [])
        except Exception as exc:
            if tentativa < 3:
                espera = (tentativa + 1) * 10
                print(f"    tentativa {tentativa+1} falhou ({exc}) — aguardando {espera}s…",
                      file=sys.stderr)
                time.sleep(espera)
            else:
                raise


def ways_da_linha(elems, ref):
    """
    Filtra ways OSM para a linha, excluindo pátio/crossover/siding.
    L17 não tem ref=17 → filtrado por name~"17|Ouro".
    """
    result = []
    for e in elems:
        tags = e.get("tags", {})
        if tags.get("service"):
            continue
        if ref == 17:
            nome = tags.get("name", "")
            if "17" in nome or "Ouro" in nome:
                result.append(e)
        else:
            if tags.get("ref") == str(ref):
                result.append(e)
    return result


# ── Montagem com suavização dos gaps (Fix 4) ─────────────────────────────────

def montar_com_suavizacao(segmentos, coords_est):
    """
    Constrói a polyline final:
    - Segmentos OSM: mantidos exatamente como vieram do Dijkstra.
    - Runs consecutivos de fallback: pontos de controle (estações)
      suavizados com Chaikin antes de costurar.
    """
    if not segmentos:
        return []

    caminho: list = []
    i = 0

    while i < len(segmentos):
        fonte, pts = segmentos[i]

        if fonte == "osm":
            caminho.extend(pts if not caminho else pts[1:])
            i += 1
        else:
            # Coleta run de fallback consecutivo
            ctrl = [[coords_est[i][0], coords_est[i][1]]]
            while i < len(segmentos) and segmentos[i][0] != "osm":
                ctrl.append([coords_est[i + 1][0], coords_est[i + 1][1]])
                i += 1
            suav = chaikin(ctrl) if len(ctrl) >= 2 else ctrl
            caminho.extend(suav if not caminho else suav[1:])

    return caminho


# ── Processamento ─────────────────────────────────────────────────────────────

def tracar_linha(ref, coords_est, elems_linha):
    """
    Traça a linha par a par com todos os fixes aplicados.
    Retorna (caminho, n_osm, n_total, segmentos, n_costura).
    segmentos: list of ("osm"|"suavizado", pts_ou_None)
    """
    ways_list = [
        [[n["lat"], n["lon"]] for n in e.get("geometry", [])]
        for e in elems_linha
        if len(e.get("geometry", [])) >= 2
    ]
    nodes, adj = construir_grafo(ways_list)

    # Fix 2: costurar componentes desconexas
    n_costura = costurar_componentes(nodes, adj) if nodes else 0

    n_total = len(coords_est) - 1
    segmentos: list = []

    for i in range(n_total):
        lat_a, lon_a = coords_est[i]
        lat_b, lon_b = coords_est[i + 1]

        seg_osm = None
        if nodes:
            k_a, d_a = snap_estacao(lat_a, lon_a, nodes)
            k_b, d_b = snap_estacao(lat_b, lon_b, nodes)
            if d_a <= SNAP_MAX_M and d_b <= SNAP_MAX_M:
                seg_osm = dijkstra_caminho(adj, nodes, k_a, k_b)
                if seg_osm and len(seg_osm) >= 2:
                    # Fix 1: rejeitar caminhos absurdamente longos
                    dist_direta = haversine_m(lat_a, lon_a, lat_b, lon_b)
                    if _comprimento_pts(seg_osm) > dist_direta * MAX_RATIO:
                        seg_osm = None

        if seg_osm and len(seg_osm) >= 2:
            segmentos.append(("osm", seg_osm))
        else:
            segmentos.append(("suavizado", None))

    caminho = montar_com_suavizacao(segmentos, coords_est)
    n_osm = sum(1 for f, _ in segmentos if f == "osm")
    return caminho, n_osm, n_total, segmentos, n_costura


def processar(elems, estacoes_por_linha):
    linhas: dict = {}

    for ref in sorted(LINHAS_ALVO):
        coords_est = estacoes_por_linha.get(ref, [])
        if not coords_est:
            print(f"  L{ref}: sem estações", file=sys.stderr)
            continue

        elems_linha = ways_da_linha(elems, ref)
        n_ways = len(elems_linha)

        caminho, n_osm, n_total, segmentos, n_costura = tracar_linha(
            ref, coords_est, elems_linha
        )

        segs = [caminho]
        med, mx = pontuar(segs, coords_est)
        n_pts = len(caminho)
        km = comprimento_total(segs) / 1000

        costura_str = f" +{n_costura}cost" if n_costura else ""
        if n_osm == n_total:
            fonte = f"trilhos-osm ({n_osm}/{n_total})"
        elif n_osm > 0:
            fonte = f"híbrido ({n_osm}/{n_total} osm)"
        else:
            fonte = "suavizado"

        print(
            f"  L{ref}: {fonte}{costura_str} | {n_ways} ways | "
            f"mediana={med:.0f}m | máx={mx:.0f}m | {n_pts} pts ({km:.1f} km)"
        )
        linhas[ref] = segs

    return linhas


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("Carregando estações…")
    estacoes_por_linha = carregar_estacoes_por_linha()
    for ref, coords in sorted(estacoes_por_linha.items()):
        print(f"  L{ref}: {len(coords)} estações")

    print("\nBuscando trilhos físicos (railway=subway|monorail) no Overpass…")
    try:
        elems = buscar_trilhos()
        print(f"  {len(elems)} ways recebidas")
    except Exception as exc:
        print(f"  Falha definitiva ao buscar trilhos: {exc}", file=sys.stderr)
        elems = []
        print("  0 ways — todas as linhas usarão fallback suavizado")

    print("\nTraçando linhas…")
    linhas = processar(elems, estacoes_por_linha)

    saida = {str(k): v for k, v in linhas.items()}
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(saida, separators=(",", ":")), encoding="utf-8")
    kb = SAIDA.stat().st_size // 1024
    print(f"\nSalvo em {SAIDA.relative_to(Path.cwd())}  ({kb} KB)")
    print("Commite data/linhas_geom.json para que o app use sem chamar Overpass.")


if __name__ == "__main__":
    main()

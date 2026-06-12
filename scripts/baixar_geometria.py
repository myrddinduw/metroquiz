#!/usr/bin/env python3
"""
Traça cada linha do Metrô SP pelos trilhos físicos do OSM (railway=subway/monorail)
usando Dijkstra por par de estações consecutivas.

Onde o OSM não cobre um par, usa segmento reto entre as duas estações.
Se a cobertura OSM total for ≥ 50%, a linha é classificada como "trilhos-osm".
Caso contrário, aplica Chaikin a todos os pontos e classifica como "suavizado".

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

LINHAS_ALVO   = {1, 2, 3, 4, 5, 15, 17}
SAIDA         = Path(__file__).parent.parent / "data" / "linhas_geom.json"
ESTACOES_PATH = Path(__file__).parent.parent / "data" / "estacoes.json"

SNAP_MAX_M    = 300   # distância máxima para encaixar estação no grafo
DIST_MED_M    = 150   # mediana estação→traçado para log/validação
DIST_MAX_M    = 400   # máx estação→traçado para log/validação
CHAIKIN_ITER  = 3     # iterações de suavização Chaikin no fallback total
NODE_PREC     = 5     # casas decimais para chave de nó (~1 m precisão)
BBOX          = "-24.1,-46.9,-23.3,-46.3"  # cobre todo o Metrô SP

# Como identificar os ways de cada linha no OSM
# ref=X funciona para todas, exceto L17 que usa name~"17"
_LINHA_REF = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 15: "15"}


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


# ── Chaikin ───────────────────────────────────────────────────────────────────

def chaikin(pts, iterations=CHAIKIN_ITER):
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
    Grafo undirected de uma lista de ways [[lat,lon],...].
    Nós com mesma coordenada arredondada são fundidos.
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
    Filtra ways OSM para a linha `ref`, excluindo trilhos de pátio/manutenção.
    L17 não tem tag ref → filtrado pelo nome.
    """
    result = []
    for e in elems:
        tags = e.get("tags", {})
        if tags.get("service"):       # pátio, crossover, siding
            continue
        if ref == 17:
            nome = tags.get("name", "")
            if "17" in nome or "Ouro" in nome:
                result.append(e)
        else:
            if tags.get("ref") == str(ref):
                result.append(e)
    return result


# ── Processamento ─────────────────────────────────────────────────────────────

def tracar_linha(ref, coords_est, elems_linha):
    """
    Traça a linha par a par: Dijkstra onde o OSM cobre, reto onde não cobre.
    Retorna (pontos_totais [[lat,lon]], n_osm, n_total).
    """
    ways = [[n["lat"], n["lon"]] for e in elems_linha for n in e.get("geometry", [])]
    # Reconstrói lista de ways como lista de listas de pontos
    ways_list = []
    for e in elems_linha:
        pts = [[n["lat"], n["lon"]] for n in e.get("geometry", [])]
        if len(pts) >= 2:
            ways_list.append(pts)

    nodes, adj = construir_grafo(ways_list)

    caminho: list = []
    n_osm = 0
    n_total = len(coords_est) - 1

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
            pts = seg_osm
            n_osm += 1
        else:
            pts = [[lat_a, lon_a], [lat_b, lon_b]]

        caminho.extend(pts if not caminho else pts[1:])

    return caminho, n_osm, n_total


def processar(elems, estacoes_por_linha):
    linhas: dict = {}

    for ref in sorted(LINHAS_ALVO):
        coords_est = estacoes_por_linha.get(ref, [])
        if not coords_est:
            print(f"  L{ref}: sem estações", file=sys.stderr)
            continue

        elems_linha = ways_da_linha(elems, ref)
        n_ways = len(elems_linha)

        caminho, n_osm, n_total = tracar_linha(ref, coords_est, elems_linha)

        segs = [caminho]
        med, mx = pontuar(segs, coords_est)
        n_pts = len(caminho)
        km = comprimento_total(segs) / 1000

        if n_osm > 0:
            fonte = f"trilhos-osm ({n_osm}/{n_total} pares)"
        else:
            # Sem nenhum par OSM: aplica Chaikin ao caminho todo para suavizar
            pts_suav = chaikin([[lat, lon] for lat, lon in coords_est])
            segs = [pts_suav]
            med, mx = pontuar(segs, coords_est)
            n_pts = len(pts_suav)
            km = comprimento_total(segs) / 1000
            fonte = "suavizado"

        print(
            f"  L{ref}: {fonte} | {n_ways} ways | "
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

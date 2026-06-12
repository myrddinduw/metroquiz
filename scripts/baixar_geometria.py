#!/usr/bin/env python3
"""
Baixa a geometria real das linhas do Metrô SP via Overpass API e salva
cada linha como uma lista de segmentos em data/linhas_geom.json.

Uso:
    python scripts/baixar_geometria.py

Commite data/linhas_geom.json depois — o app.py carrega desse arquivo
e só chama o Overpass ao vivo se ele estiver ausente.

Formato salvo:
    {"1": [[[lat, lon], ...], ...], ...}   (lista de segmentos por linha)
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

LINHAS_ALVO = {1, 2, 3, 4, 5, 15, 17}
SAIDA = Path(__file__).parent.parent / "data" / "linhas_geom.json"
ESTACOES_PATH = Path(__file__).parent.parent / "data" / "estacoes.json"
DIST_MAX_M = 250   # limiar: maioria das estações deve estar abaixo disso
DIST_PIOR_M = 500  # nenhuma estação pode estar além disso (sinal de relação OSM errada)


# ── Geometria ─────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def dist_ponto_segmento(plat: float, plon: float,
                        alat: float, alon: float,
                        blat: float, blon: float) -> float:
    """Distância em metros de P ao segmento AB (aproximação plana local)."""
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


def dist_ponto_geometria(plat: float, plon: float, segs: list) -> float:
    """Menor distância em metros de um ponto à geometria (coleção de segmentos)."""
    min_d = float("inf")
    for seg in segs:
        for i in range(len(seg) - 1):
            d = dist_ponto_segmento(
                plat, plon, seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1]
            )
            min_d = min(min_d, d)
    return min_d


def comprimento_total(segs: list) -> float:
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += haversine_m(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
    return total


# ── Estações ──────────────────────────────────────────────────────────────────

def carregar_estacoes_por_linha() -> dict:
    """Retorna {ref: [(lat, lon), ...]} ordenado pela sequência da linha."""
    with open(ESTACOES_PATH, encoding="utf-8") as f:
        todas = json.load(f)
    por_linha: dict = {}
    for e in todas:
        for linha in e["linhas"]:
            por_linha.setdefault(linha, []).append(e)
    result = {}
    for ref, estacoes in por_linha.items():
        if ref in LINHAS_ALVO:
            ordenadas = sorted(estacoes, key=lambda e: e["ordem"][str(ref)])
            result[ref] = [(e["lat"], e["lon"]) for e in ordenadas]
    return result


# ── OSM ───────────────────────────────────────────────────────────────────────

def segs_da_relacao(elem: dict) -> list:
    segs = []
    for m in elem.get("members", []):
        if m.get("type") != "way" or m.get("role") in ("stop", "platform"):
            continue
        pts = [[n["lat"], n["lon"]] for n in m.get("geometry", [])]
        if len(pts) >= 2:
            segs.append(pts)
    return segs


def _fetch_linha(ref: int) -> list:
    """Busca relações subway/monorail para uma linha dentro da área de São Paulo."""
    query = (
        f"[out:json][timeout:60];"
        f"area[\"name\"=\"São Paulo\"][\"admin_level\"=\"8\"]->.a;"
        f"relation[\"route\"~\"subway|monorail\"][\"ref\"=\"{ref}\"](area.a);"
        f"out geom;"
    )
    url = ("https://overpass-api.de/api/interpreter?"
           + urllib.parse.urlencode({"data": query}))
    for tentativa in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "metroquiz/1.0"})
            with urllib.request.urlopen(req, timeout=65) as resp:
                return json.loads(resp.read()).get("elements", [])
        except Exception as exc:
            if tentativa < 3:
                espera = (tentativa + 1) * 8
                print(f"    tentativa {tentativa+1} falhou ({exc}) — aguardando {espera}s…",
                      file=sys.stderr)
                time.sleep(espera)
            else:
                raise


def buscar_overpass() -> dict:
    elementos = []
    for ref in sorted(LINHAS_ALVO):
        print(f"  buscando L{ref}…", flush=True)
        try:
            elems = _fetch_linha(ref)
            print(f"    {len(elems)} relações", flush=True)
            elementos.extend(elems)
        except Exception as exc:
            print(f"    L{ref}: falha definitiva — {exc}", file=sys.stderr)
        time.sleep(3)
    return {"elements": elementos}


# ── Validação e fallback ──────────────────────────────────────────────────────

def validar_e_construir(ref: int, segs_osm: list, coords_est: list) -> tuple:
    """
    Valida que a maioria das estações está ≤ DIST_MAX_M da geometria OSM.
    Retorna (segs, fonte_str).
    """
    if segs_osm and coords_est:
        distancias = [dist_ponto_geometria(lat, lon, segs_osm) for lat, lon in coords_est]
        aprovadas = sum(1 for d in distancias if d <= DIST_MAX_M)
        pior = max(distancias)
        if aprovadas > len(distancias) / 2 and pior <= DIST_PIOR_M:
            return segs_osm, f"OSM (pior dist: {pior:.0f} m)"
        print(
            f"  L{ref}: OSM reprovado ({aprovadas}/{len(distancias)} ≤ {DIST_MAX_M} m, "
            f"pior {pior:.0f} m) → fallback para segmentos pelas estações",
            file=sys.stderr,
        )

    if coords_est:
        segs_fallback = [[[lat, lon] for lat, lon in coords_est]]
        return segs_fallback, "estações (fallback)"
    return [], "sem dados"


# ── Processamento ─────────────────────────────────────────────────────────────

def processar(resultado: dict, estacoes_por_linha: dict) -> dict:
    por_ref: dict = {}
    for elem in resultado.get("elements", []):
        if elem.get("type") != "relation":
            continue
        try:
            ref = int(elem.get("tags", {}).get("ref", ""))
        except (ValueError, TypeError):
            continue
        if ref in LINHAS_ALVO:
            por_ref.setdefault(ref, []).append(elem)

    linhas: dict = {}
    for ref in sorted(LINHAS_ALVO):
        coords_est = estacoes_por_linha.get(ref, [])
        elems = por_ref.get(ref, [])

        segs_osm: list = []
        variantes_str = f"{len(elems)} variante{'s' if len(elems) != 1 else ''}"
        if elems:
            melhor = max(elems, key=lambda e: comprimento_total(segs_da_relacao(e)))
            segs_osm = segs_da_relacao(melhor)
        else:
            variantes_str = "0 variantes"

        segs, fonte = validar_e_construir(ref, segs_osm, coords_est)
        if not segs:
            print(f"  L{ref}: {variantes_str} → sem segmentos", file=sys.stderr)
            continue

        km = comprimento_total(segs) / 1000
        n_pts = sum(len(s) for s in segs)
        print(f"  L{ref}: {variantes_str} → {len(segs)} segs "
              f"→ {n_pts} pts ({km:.1f} km) [{fonte}]")
        linhas[ref] = segs

    return linhas


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Carregando estações…")
    estacoes_por_linha = carregar_estacoes_por_linha()
    for ref, coords in sorted(estacoes_por_linha.items()):
        print(f"  L{ref}: {len(coords)} estações")

    print("\nConsultando Overpass API (route=subway|monorail, área SP)…")
    try:
        resultado = buscar_overpass()
    except Exception as exc:
        sys.exit(f"Erro ao consultar Overpass: {exc}")

    n_elem = len(resultado.get("elements", []))
    print(f"  {n_elem} elementos recebidos\n")

    print("Processando e validando geometria…")
    linhas = processar(resultado, estacoes_por_linha)

    saida = {str(k): v for k, v in linhas.items()}
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(saida, separators=(",", ":")), encoding="utf-8")
    kb = SAIDA.stat().st_size // 1024
    print(f"\nSalvo em {SAIDA.relative_to(Path.cwd())}  ({kb} KB)")
    print("Commite data/linhas_geom.json para que o app use sem chamar Overpass.")


if __name__ == "__main__":
    main()

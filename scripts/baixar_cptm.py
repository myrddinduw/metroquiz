#!/usr/bin/env python3
"""
Baixa estações e geometria das linhas CPTM via Overpass API.
Salva em data/estacoes_cptm.json e data/linhas_geom_cptm.json.

Uso:
    python scripts/baixar_cptm.py

Commite os dois arquivos depois — app.py carrega deles quando
o Modo CPTM está ativado.
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

LINHAS_CPTM = [7, 8, 9, 10, 11, 12, 13]
DIR = Path(__file__).parent.parent / "data"
SAIDA_ESTS = DIR / "estacoes_cptm.json"
SAIDA_GEOM = DIR / "linhas_geom_cptm.json"
DIST_MAX_M = 250
DIST_PIOR_M = 500

# Aliases: nome OSM lowercase → nome canônico (mesmo que game.py)
_ALIAS = {
    "barra funda":             "Palmeiras-Barra Funda",
    "estação barra funda":     "Palmeiras-Barra Funda",
    "estacao barra funda":     "Palmeiras-Barra Funda",
    "palmeiras - barra funda": "Palmeiras-Barra Funda",
    "palmeiras–barra funda":   "Palmeiras-Barra Funda",
    "itaquera":                "Corinthians-Itaquera",
    "corinthians - itaquera":  "Corinthians-Itaquera",
}


# ── Geometria ─────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def dist_ponto_seg(plat, plon, alat, alon, blat, blon):
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


def dist_ponto_geom(plat, plon, segs):
    min_d = float("inf")
    for seg in segs:
        for i in range(len(seg) - 1):
            d = dist_ponto_seg(plat, plon, seg[i][0], seg[i][1], seg[i+1][0], seg[i+1][1])
            min_d = min(min_d, d)
    return min_d


def comprimento(segs):
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += haversine_m(seg[i][0], seg[i][1], seg[i+1][0], seg[i+1][1])
    return total


# ── Nomes ─────────────────────────────────────────────────────────────────────

def norm(nome: str) -> str:
    nome = nome.strip()
    for pref in ("Estação ", "Estacao ", "Est. "):
        if nome.startswith(pref):
            nome = nome[len(pref):].strip()
    return nome.strip()


def canonico(nome: str) -> str:
    n = norm(nome)
    return _ALIAS.get(n.lower(), n)


# ── OSM ───────────────────────────────────────────────────────────────────────

def _fetch(ref: int) -> list:
    """Busca relações de trem com ref=N no bbox do estado de SP."""
    query = (
        f"[out:json][timeout:120][bbox:-25.5,-50.0,-20.0,-44.0];"
        f"relation[\"route\"=\"train\"][\"ref\"=\"{ref}\"];"
        f"out geom;"
    )
    url = ("https://overpass-api.de/api/interpreter?"
           + urllib.parse.urlencode({"data": query}))
    for tentativa in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "metroquiz-cptm/1.0"})
            with urllib.request.urlopen(req, timeout=130) as resp:
                return json.loads(resp.read()).get("elements", [])
        except Exception as exc:
            if tentativa < 3:
                espera = (tentativa + 1) * 10
                print(f"    tentativa {tentativa+1} falhou ({exc}) — aguardando {espera}s…",
                      file=sys.stderr)
                time.sleep(espera)
            else:
                raise


def _fetch_nomes(node_refs: list) -> dict:
    """
    Busca os tags (nome) de uma lista de IDs de nó.
    Com out geom, os nós-membro de relações têm lat/lon mas não têm tags —
    é preciso buscá-los separadamente.
    Retorna {node_id: nome}.
    """
    if not node_refs:
        return {}
    ids = ",".join(str(r) for r in node_refs)
    query = f"[out:json][timeout:30];node(id:{ids});out tags;"
    url = ("https://overpass-api.de/api/interpreter?"
           + urllib.parse.urlencode({"data": query}))
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "metroquiz-cptm/1.0"})
            with urllib.request.urlopen(req, timeout=35) as resp:
                elems = json.loads(resp.read()).get("elements", [])
            return {e["id"]: e.get("tags", {}).get("name", "") for e in elems}
        except Exception as exc:
            if tentativa < 2:
                time.sleep((tentativa + 1) * 5)
            else:
                print(f"  _fetch_nomes falhou: {exc}", file=sys.stderr)
                return {}


def extrair_paradas(elem: dict, nomes_por_ref: dict) -> list:
    """
    Extrai nós stop/station da relação.
    nomes_por_ref: {node_id: nome} obtido via _fetch_nomes().
    Retorna [(nome_canônico, lat, lon, posição), ...] em ordem.
    """
    paradas = []
    pos = 0
    vistas: set = set()
    for m in elem.get("members", []):
        if m.get("type") != "node":
            continue
        role = m.get("role", "")
        if role not in ("stop", "stop_entry_only", "stop_exit_only", "station"):
            continue
        lat = m.get("lat")
        lon = m.get("lon")
        if lat is None or lon is None:
            continue
        nome_raw = nomes_por_ref.get(m["ref"], "")
        if not nome_raw:
            continue
        nome_c = canonico(nome_raw)
        chave = (nome_c, round(lat, 4), round(lon, 4))
        if chave in vistas:
            continue
        vistas.add(chave)
        pos += 1
        paradas.append((nome_c, lat, lon, pos))
    return paradas


def segs_da_relacao(elem: dict) -> list:
    segs = []
    for m in elem.get("members", []):
        if m.get("type") != "way" or m.get("role") in ("stop", "platform"):
            continue
        pts = [[n["lat"], n["lon"]] for n in m.get("geometry", [])]
        if len(pts) >= 2:
            segs.append(pts)
    return segs


# ── Validação ─────────────────────────────────────────────────────────────────

def validar(ref, segs, coords):
    if not segs or not coords:
        return False, float("inf")
    dists = [dist_ponto_geom(lat, lon, segs) for lat, lon in coords]
    aprovadas = sum(1 for d in dists if d <= DIST_MAX_M)
    pior = max(dists)
    ok = aprovadas > len(dists) / 2 and pior <= DIST_PIOR_M
    return ok, pior


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ests_dict: dict = {}   # nome_canônico → {nome, lat, lon, linhas, ordem}
    geom: dict = {}        # ref → [[seg]]

    for ref in LINHAS_CPTM:
        print(f"\nL{ref} — buscando no Overpass…", flush=True)
        try:
            elems = _fetch(ref)
        except Exception as exc:
            print(f"  L{ref}: falha definitiva — {exc}", file=sys.stderr)
            time.sleep(3)
            continue

        relacoes = [e for e in elems if e.get("type") == "relation"]
        print(f"  {len(relacoes)} relação(ões) encontrada(s)")
        if not relacoes:
            print(f"  L{ref}: sem relações OSM", file=sys.stderr)
            time.sleep(3)
            continue

        melhor = max(relacoes, key=lambda e: comprimento(segs_da_relacao(e)))
        tags = melhor.get("tags", {})
        print(f"  Usando relação {melhor.get('id')} — name={tags.get('name','?')}")

        # Coleta refs dos nós stop para buscar os nomes separadamente
        stop_refs = [
            m["ref"] for m in melhor.get("members", [])
            if m.get("type") == "node"
            and m.get("role", "") in ("stop", "stop_entry_only", "stop_exit_only", "station")
        ]
        print(f"  Buscando nomes de {len(stop_refs)} nós stop…", flush=True)
        nomes_por_ref = _fetch_nomes(stop_refs)
        time.sleep(2)

        paradas = extrair_paradas(melhor, nomes_por_ref)
        segs = segs_da_relacao(melhor)
        coords = [(lat, lon) for _, lat, lon, _ in paradas]

        ok, pior = validar(ref, segs, coords)
        if ok:
            print(f"  Geometria OSM aprovada (pior dist: {pior:.0f} m)")
            geom[ref] = segs
        else:
            print(f"  Geometria OSM reprovada (pior {pior:.0f} m) → segmentos pelas estações",
                  file=sys.stderr)
            if coords:
                geom[ref] = [[[lat, lon] for lat, lon in coords]]

        print(f"  Paradas ({len(paradas)}): {[p[0] for p in paradas]}")

        for nome_c, lat, lon, pos in paradas:
            if nome_c not in ests_dict:
                ests_dict[nome_c] = {"nome": nome_c, "lat": lat, "lon": lon,
                                     "linhas": [], "ordem": {}}
            e = ests_dict[nome_c]
            if ref not in e["linhas"]:
                e["linhas"].append(ref)
                e["linhas"].sort()
            e["ordem"][str(ref)] = pos

        time.sleep(4)

    # Salva estações
    ests_lista = sorted(
        ests_dict.values(),
        key=lambda e: (e["linhas"][0], e["ordem"].get(str(e["linhas"][0]), 0))
    )
    DIR.mkdir(parents=True, exist_ok=True)
    SAIDA_ESTS.write_text(
        json.dumps(ests_lista, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ {len(ests_lista)} estações → {SAIDA_ESTS.name}")

    # Salva geometria
    geom_out = {str(k): v for k, v in geom.items()}
    SAIDA_GEOM.write_text(
        json.dumps(geom_out, separators=(",", ":")), encoding="utf-8"
    )
    print(f"✓ {len(geom_out)} linhas com geometria → {SAIDA_GEOM.name}")

    print("\n⚠ Revise os nomes antes de commitar!")
    print("  Verifique baldeações: Luz, Brás, Palmeiras-Barra Funda,")
    print("  Tatuapé, Corinthians-Itaquera, Pinheiros, Santo Amaro, Morumbi.")


if __name__ == "__main__":
    main()

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


# ── Geometria ─────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def comprimento_total(segs: list) -> float:
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += haversine_m(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
    return total


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
    """Busca todas as relações de rota para uma linha (por ref), com retries."""
    query = (
        f"[out:json][timeout:45];"
        f"relation[\"network\"=\"Metrô de São Paulo\"][\"type\"=\"route\"]"
        f"[\"ref\"=\"{ref}\"];"
        f"out geom;"
    )
    url = ("https://overpass-api.de/api/interpreter?"
           + urllib.parse.urlencode({"data": query}))
    for tentativa in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "metroquiz/1.0"})
            with urllib.request.urlopen(req, timeout=50) as resp:
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


def processar(resultado: dict) -> dict:
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
    ausentes = LINHAS_ALVO.copy()
    for ref in sorted(por_ref):
        elems = por_ref[ref]
        # Escolhe a relação com maior comprimento total de geometria
        melhor = max(elems, key=lambda e: comprimento_total(segs_da_relacao(e)))
        variantes_str = f"{len(elems)} variante{'s' if len(elems) > 1 else ''}"
        segs = segs_da_relacao(melhor)
        if not segs:
            print(f"  L{ref}: sem segmentos ({variantes_str})", file=sys.stderr)
            continue
        km = comprimento_total(segs) / 1000
        n_pts = sum(len(s) for s in segs)
        print(f"  L{ref}: {variantes_str} → {len(segs)} segmentos → "
              f"{n_pts} pontos ({km:.1f} km)")
        linhas[ref] = segs
        ausentes.discard(ref)

    if ausentes:
        print(f"\nAVISO: sem geometria OSM para L{sorted(ausentes)} "
              "(app usará segmento reto como fallback)", file=sys.stderr)
    return linhas


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Consultando Overpass API (por linha, rede 'Metrô de São Paulo')…")
    try:
        resultado = buscar_overpass()
    except Exception as exc:
        sys.exit(f"Erro ao consultar Overpass: {exc}")

    n_elem = len(resultado.get("elements", []))
    print(f"  {n_elem} elementos recebidos\n")

    print("Processando geometria…")
    linhas = processar(resultado)

    saida = {str(k): v for k, v in linhas.items()}
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(saida, separators=(",", ":")), encoding="utf-8")
    kb = SAIDA.stat().st_size // 1024
    print(f"\nSalvo em {SAIDA.relative_to(Path.cwd())}  ({kb} KB)")
    print("Commite data/linhas_geom.json para que o app use sem chamar Overpass.")


if __name__ == "__main__":
    main()

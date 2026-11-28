"""Universe emiten BEI untuk screener pasar penuh.

Daftar disimpan di `data/idx_universe.json` (hasil `tools/build_universe.py`)
berisi ±950 kode 4 huruf. Screener memvalidasi tiap kode lewat data harga
Yahoo; kode tanpa data (delisted/suspensi panjang) dilewati dan dilaporkan
secara jujur, bukan diisi data palsu.

Pemetaan ticker→sektor (gaya yfinance, 11 sektor) disimpan di
`data/idx_sectors.json` (dibangun dari daftar publik tweenvest.com,
17 Nov 2026; 916 emiten, 29 kode universe tidak terpetakan karena sudah
tidak aktif di listing itu). Kode tanpa sektor dilaporkan "" — bukan
ditebak — dan pemanggil memakai statistik global sebagai fallback.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "data", "idx_universe.json")
SECTORS_PATH = os.path.join(_ROOT, "data", "idx_sectors.json")


@lru_cache(maxsize=1)
def _payload() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"source": "tidak tersedia", "fetched_at": "", "count": 0,
                "stocks": []}
    if not isinstance(data, dict) or not isinstance(data.get("stocks"), list):
        return {"source": "format rusak", "fetched_at": "", "count": 0,
                "stocks": []}
    return data


def stocks() -> list[dict]:
    """Daftar [{code, name, board}] urut kode."""
    return list(_payload().get("stocks") or [])


def codes() -> list[str]:
    return [s["code"] for s in stocks()]


def main_board_codes() -> list[str]:
    """Daftar kode emiten Papan Utama BEI (~293 emiten likuid/besar) untuk scan cepat."""
    return [s["code"] for s in stocks() if (s.get("board") or "").strip().lower() == "utama"]


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    return {s["code"]: s for s in stocks()}


def name_of(code: str) -> str:
    return (_index().get((code or "").upper()) or {}).get("name", "")


def board_of(code: str) -> str:
    return (_index().get((code or "").upper()) or {}).get("board", "")


def meta() -> dict:
    p = _payload()
    return {"source": p.get("source", ""), "fetched_at": p.get("fetched_at", ""),
            "count": len(p.get("stocks") or [])}


@lru_cache(maxsize=1)
def _sector_payload() -> dict:
    try:
        with open(SECTORS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def _sector_map() -> dict[str, str]:
    sectors = _sector_payload().get("sectors")
    if not isinstance(sectors, dict):
        return {}
    return {str(k).strip().upper(): str(v).strip()
            for k, v in sectors.items()
            if isinstance(v, str) and str(v).strip()}


def sector_of(code: str) -> str:
    """Sektor gaya yfinance untuk satu kode, atau "" bila tidak terpetakan.

    Tidak pernah mengarang: kode yang tidak ada di `data/idx_sectors.json`
    (mis. sudah delisting) mengembalikan string kosong.
    """
    return _sector_map().get((code or "").strip().upper(), "")


def sectors_meta() -> dict:
    p = _sector_payload()
    return {"source": p.get("source", ""), "fetched_at": p.get("fetched_at", ""),
            "count": len(_sector_map())}


def available() -> bool:
    return len(codes()) > 0

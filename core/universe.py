"""Universe emiten BEI untuk screener pasar penuh.

Daftar disimpan di `data/idx_universe.json` (hasil `tools/build_universe.py`)
berisi ±950 kode 4 huruf. Screener memvalidasi tiap kode lewat data harga
Yahoo; kode tanpa data (delisted/suspensi panjang) dilewati dan dilaporkan
secara jujur, bukan diisi data palsu.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "data", "idx_universe.json")


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


def available() -> bool:
    return len(codes()) > 0

"""Penanda kualitas fundamental untuk kandidat BELI (P15).

Menilai tiga hal yang bisa dibaca dari data Yahoo — laba, ROE, dan valuasi —
BUKAN probabilitas naik. Ambangnya ditulis di sini supaya bisa dibantah:

    ROE_MIN  = 8%     di bawah ini = kualitas laba lemah
    PER_MAX  = 25x    di atas ini  = mahal
    PBV_MAX  = 4x     di atas ini  = mahal

Data tanpa ketiga angka itu dianggap "belum dinilai" (None), bukan lolos dan
bukan ditolak — supaya `/sinyal beli bagus` tidak diam-diam membuang emiten
yang fundamentalnya sekadar gagal diambil.
"""
from __future__ import annotations

import json
import os

from core import fundamental

ROE_MIN = 8.0
PER_MAX = 25.0
PBV_MAX = 4.0
DAY = 24 * 3600
CACHE_VERSION = fundamental.CACHE_VERSION

MARK = {True: "✅", False: "⚠️", None: "❔"}


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def judge(fund_raw: dict | None) -> dict:
    """Putuskan kualitas ringkas satu emiten.

    Return {"ok": True/False/None, "label": str, "reason": str, "bits": [str]}
    """
    raw = fund_raw or {}
    per = _f(raw.get("per"))
    pbv = _f(raw.get("pbv"))
    roe = fundamental.normalize_roe(raw.get("roe"), unit=raw.get("roe_unit"))
    bits = []
    if per is not None:
        bits.append(f"PER {per:.1f}".replace(".", ","))
    if pbv is not None:
        bits.append(f"PBV {pbv:.2f}".replace(".", ","))
    if roe is not None:
        bits.append(f"ROE {roe:.1f}%".replace(".", ","))

    if per is None and pbv is None and roe is None:
        return {"ok": None, "label": "belum dinilai", "bits": [],
                "reason": "data fundamental tidak tersedia"}
    if (per is not None and per < 0) or (roe is not None and roe < 0):
        return {"ok": False, "label": "merugi", "bits": bits,
                "reason": "laba negatif (PER/ROE < 0)"}
    if roe is not None and roe < ROE_MIN:
        return {"ok": False, "label": "ROE tipis", "bits": bits,
                "reason": f"ROE {roe:.1f}% < ambang {ROE_MIN:.0f}%"
                          .replace(".", ",")}
    if (per is not None and per > PER_MAX) or (pbv is not None and pbv > PBV_MAX):
        return {"ok": False, "label": "mahal", "bits": bits,
                "reason": "PER/PBV di atas ambang"}
    return {"ok": True, "label": "sehat", "bits": bits,
            "reason": "laba positif, ROE cukup, valuasi tidak ekstrem"}


def passes(kval: dict | None) -> bool:
    """Lolos filter hanya bila dinilai DAN sehat (None tidak lolos)."""
    return bool(kval and kval.get("ok") is True)


def mark(kval: dict | None) -> str:
    return MARK.get((kval or {}).get("ok"), "❔")


def short(kval: dict | None) -> str:
    """Potongan satu baris untuk daftar sinyal: '✅ sehat · ROE 18,0%'."""
    if not kval:
        return ""
    text = f"{mark(kval)} {kval.get('label') or '—'}"
    if kval.get("bits"):
        text += " · " + " · ".join(kval["bits"])
    return text


# ------------------------------------------------------- cache harian (JSON)
def _path(cache_dir: str, ticker: str, day: str) -> str:
    t = "".join(ch for ch in str(ticker).upper() if ch.isalnum())
    return os.path.join(cache_dir, f"fund_{t}_{day}.json")


def load_cached(cache_dir: str, ticker: str, day: str) -> dict | None:
    """Fundamental yang sudah diambil pada tanggal (WIB) yang sama.

    File cache JSON versi lama (tanpa ``_v == CACHE_VERSION``) dianggap
    kedaluwarsa agar ROE desimal lama (0.18) otomatis di-fetch/dinormalisasi ulang.
    """
    if not cache_dir:
        return None
    path = _path(cache_dir, ticker, day)
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("_v") != CACHE_VERSION:
        return None
    return payload


def save_cached(cache_dir: str, ticker: str, day: str, fund_raw: dict) -> bool:
    """Simpan fundamental harian secara atomik (tmp -> replace)."""
    if not cache_dir or not fund_raw:
        return False
    payload = dict(fund_raw)
    payload["_v"] = CACHE_VERSION
    if payload.get("roe") is not None and payload.get("roe_unit") != "pct":
        payload["roe"] = fundamental.normalize_roe(
            payload.get("roe"), unit=payload.get("roe_unit")
        )
    payload.setdefault("roe_unit", "pct")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = _path(cache_dir, ticker, day)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False

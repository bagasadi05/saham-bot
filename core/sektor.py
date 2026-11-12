"""Kekuatan relatif sektor dari indeks BEI (`/sektor`, P18).

Sumber satu-satunya yang tersedia: `GetIndexSummary` BEI (lewat
`core.bandar.get_index_summary`). Payload itu **tidak** punya dokumentasi
resmi yang stabil, jadi modul ini sengaja defensif:

  - persentase diambil dari field persen bila ada; kalau tidak, dihitung dari
    `IndexClose` dan `IndexPrevious`/`IndexChange` (poin);
  - baris tanpa angka yang bisa dihitung **dibuang**, bukan diisi 0;
  - kalau tidak ada satu pun sektor yang bisa dihitung, kembalikan
    `{"ok": False, "reason": ...}` dan bot berkata "tidak tersedia".

Nama sektor memakai peta kode IDX-IC yang umum; kode yang tidak dikenal tetap
ditampilkan apa adanya supaya tidak salah label.
"""
from __future__ import annotations

import math

# IDX-IC (klasifikasi industri BEI). Kode yang tidak ada di peta ditampilkan
# apa adanya — lebih baik "IDXXYZ" daripada label sektor yang salah.
SECTOR_NAMES = {
    "IDXENERGY": "Energi",
    "IDXBASIC": "Barang Baku",
    "IDXBASICINDUSTRY": "Barang Baku",
    "IDXINDUSTRY": "Perindustrian",
    "IDXINDUSTRIALS": "Perindustrian",
    "IDXCONSUMER": "Konsumen Non-Primer",
    "IDXCONSUMERGOODS": "Konsumen Primer",
    "IDXCONSUMERNONCYCLICAL": "Konsumen Primer",
    "IDXCONSUMERCYCLICAL": "Konsumen Non-Primer",
    "IDXHEALTH": "Kesehatan",
    "IDXHEALTHCARE": "Kesehatan",
    "IDXFINANCE": "Keuangan",
    "IDXPROPERTY": "Properti & Real Estat",
    "IDXINFRA": "Infrastruktur",
    "IDXINFRASTRUCTURE": "Infrastruktur",
    "IDXTRANS": "Transportasi & Logistik",
    "IDXTECHNO": "Teknologi",
    "IDXTECHNOLOGY": "Teknologi",
}

# Indeks luas yang bukan sektor — jangan ditampilkan sebagai "sektor".
BROAD_CODES = {
    "COMPOSITE", "IHSG", "LQ45", "IDX30", "IDX80", "IDXESGL", "IDXG30",
    "IDXHIDIV20", "IDXQ30", "IDXV30", "JII", "JII70", "KOMPAS100", "MNC36",
    "BISNIS27", "DBX", "INFOBANK15", "SMCCOMPOSITE", "PRIMBANK8",
}

_PCT_KEYS = ("IndexPercent", "Percent", "Percentage", "ChangePercent",
             "IndexChangePercent", "PctChange")
_CLOSE_KEYS = ("IndexClose", "Close", "Last", "IndexLast")
_PREV_KEYS = ("IndexPrevious", "Previous", "PrevClose", "PreviousClose")
_CHANGE_KEYS = ("IndexChange", "Change")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _first(row: dict, keys) -> float | None:
    for k in keys:
        if k in row:
            v = _f(row.get(k))
            if v is not None:
                return v
    return None


def pct_change(row: dict) -> float | None:
    """Persentase perubahan indeks, atau None bila tidak bisa dihitung."""
    direct = _first(row, _PCT_KEYS)
    if direct is not None:
        return direct
    close = _first(row, _CLOSE_KEYS)
    prev = _first(row, _PREV_KEYS)
    if close and prev and prev > 0:
        return (close / prev - 1.0) * 100.0
    change = _first(row, _CHANGE_KEYS)
    if close and change is not None:
        # `IndexChange` diperlakukan sebagai poin; kalau ternyata persen,
        # selisih hasilnya kecil (x/(close-x) vs x/close) dan tetap bertanda sama.
        base = close - change
        if base > 0:
            return change / base * 100.0
    return None


def sector_rows(rows: list[dict] | None) -> list[dict]:
    """Baris sektor yang bisa dihitung, urut dari terkuat.

    Return [] bila payload tidak memuat satu pun kode sektor — pemanggil
    berkata "tidak tersedia", bukan menampilkan daftar kosong tanpa alasan.
    """
    out: list[dict] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("IndexCode") or row.get("Code") or "").upper()
        if not code or code in BROAD_CODES or code in seen:
            continue
        pct = pct_change(row)
        if pct is None:
            continue
        seen.add(code)
        # Hanya kode sektor yang dikenal. Payload BEI juga memuat indeks lain
        # (mis. IDXLQ45LCL); menyebutnya "sektor" akan menyesatkan.
        if code not in SECTOR_NAMES:
            unknown.append(code)
            continue
        out.append({
            "code": code,
            "name": SECTOR_NAMES[code],
            "pct": round(pct, 2),
            "close": _first(row, _CLOSE_KEYS),
            "date": str(row.get("Date") or "")[:10],
        })
    out.sort(key=lambda r: (-r["pct"], r["code"]))
    return out


def summarize(rows: list[dict] | None) -> dict:
    """Ringkasan siap tampil: kuat/lemah + jumlah sektor yang terbaca."""
    secs = sector_rows(rows)
    if not secs:
        return {"ok": False, "sectors": [],
                "reason": "payload BEI tidak memuat indeks sektor yang dikenal "
                          "(kode lain diabaikan, bukan dianggap sektor)"}
    pos = sum(1 for s in secs if s["pct"] > 0)
    neg = sum(1 for s in secs if s["pct"] < 0)
    return {
        "ok": True,
        "sectors": secs,
        "n": len(secs),
        "n_positive": pos,
        "n_negative": neg,
        "best": secs[0],
        "worst": secs[-1],
        "date": next((s["date"] for s in secs if s["date"]), ""),
    }

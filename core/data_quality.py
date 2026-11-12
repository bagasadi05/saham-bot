"""Kualitas data OHLCV: menandai masalah, bukan membuang bar diam-diam.

Dipakai `core/data.py` setelah validasi dasar. Hasilnya disimpan di
`df.attrs["quality"]` dan bisa diringkas satu baris untuk laporan /analisis.

Prinsip: data yang mencurigakan tetap dipakai (atau dilaporkan), tetapi
pembaca diberi tahu — bukan dihilangkan tanpa jejak.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from core import marketcal

MIN_BARS = 30               # di bawah ini indikator tren belum layak
STALE_TRADING_DAYS = 5      # data dianggap basi setelah 5 hari bursa
MAX_CALENDAR_GAP = 10       # celah >10 hari kalender = ada sesi hilang
JUMP_PCT = 25.0             # lompatan harga yang perlu dijelaskan aksi korporasi

_LABELS = {
    "no_data": "tanpa data",
    "short_history": "riwayat pendek",
    "volume_nol": "bar volume 0",
    "unexplained_jump": "lompatan >25%",
    "bad_ohlc": "OHLC mustahil",
    "session_gap": "celah sesi",
    "stale_days": "data basi",
}


def check(df: pd.DataFrame | None, today: dt.date | None = None,
          min_bars: int = MIN_BARS) -> dict:
    """Periksa satu DataFrame OHLCV; return {ok, flags, notes, rows, last_bar}."""
    if df is None or len(df) == 0:
        return {"ok": False, "flags": {"no_data": 1},
                "notes": ["Tidak ada bar"], "rows": 0, "last_bar": ""}

    flags: dict[str, float | int] = {}
    notes: list[str] = []
    n = len(df)

    if n < min_bars:
        flags["short_history"] = n
        notes.append(f"riwayat hanya {n} bar")

    close = pd.to_numeric(df["close"], errors="coerce") if "close" in df else None
    if "volume" in df:
        vol = pd.to_numeric(df["volume"], errors="coerce")
        zeros = int((vol <= 0).sum())
        if zeros:
            flags["volume_nol"] = zeros

    if close is not None and close.notna().sum() > 1:
        jumps = close.pct_change(fill_method=None).abs() > (JUMP_PCT / 100.0)
        nj = int(jumps.sum())
        if nj:
            flags["unexplained_jump"] = nj

    if {"high", "low"}.issubset(df.columns):
        bad = int(((df["high"] < df["low"]) | (df["close"] <= 0)).sum())
        if bad:
            flags["bad_ohlc"] = bad

    if isinstance(df.index, pd.DatetimeIndex) and n > 1:
        gaps = pd.Series(df.index).diff().dt.days
        ng = int((gaps > MAX_CALENDAR_GAP).sum())
        if ng:
            flags["session_gap"] = ng

    try:
        last = df.index[-1]
        last_date = last.date() if hasattr(last, "date") else last
        today = today or marketcal.now_wib().date()
        stale = marketcal.trading_days_between(last_date, today)
        if stale > STALE_TRADING_DAYS:
            flags["stale_days"] = stale
    except Exception:
        pass

    return {
        "ok": not flags,
        "flags": flags,
        "notes": notes,
        "rows": n,
        "last_bar": str(df.index[-1])[:10],
    }


def summary_line(quality: dict | None) -> str:
    """Satu baris HTML untuk laporan; kosong bila data bersih."""
    if not quality or quality.get("ok"):
        return ""
    parts: list[str] = []
    for key, value in (quality.get("flags") or {}).items():
        label = _LABELS.get(key, key)
        if key == "stale_days":
            parts.append(f"data {value} hari bursa lalu")
        elif key == "short_history":
            parts.append(f"riwayat pendek ({value} bar)")
        elif key == "no_data":
            parts.append("tanpa data")
        else:
            parts.append(f"{value} {label}")
    return "🧪 <i>Kualitas data: " + ", ".join(parts) + ".</i>\n"

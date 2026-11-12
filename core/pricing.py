"""Aturan harga saham BEI: fraksi tick & pembulatan level order.

Satu sumber kebenaran supaya level entry/SL/TP yang ditampilkan bot bisa
benar-benar dipasang di aplikasi broker (Stockbit dkk). Sebelumnya `saran.py`
membulatkan ke rupiah bulat, sehingga harga seperti 9.013 bukan fraksi sah
untuk emiten di atas Rp5.000 (tick Rp25).

Fraksi harga IDX (Peraturan BEI, tiga desimal di bawah Rp200):
    < 200        → 1
    200–<500     → 2
    500–<2.000   → 5
    2.000–<5.000 → 10
    ≥ 5.000      → 25
"""
from __future__ import annotations

import datetime as dt
import math


def tick_size(price: float) -> float:
    """Fraksi harga IDX untuk satu harga."""
    p = float(price)
    if p < 200:
        return 1.0
    if p < 500:
        return 2.0
    if p < 2000:
        return 5.0
    if p < 5000:
        return 10.0
    return 25.0


def round_tick(price: float, tick: float | None = None) -> float:
    """Bulatkan ke tick terdekat (half-up, bukan banker's rounding)."""
    t = tick if tick else tick_size(price)
    return math.floor(float(price) / t + 0.5) * t


def ceil_tick(price: float, tick: float | None = None) -> float:
    """Bulatkan ke atas ke tick berikutnya (untuk batas 'jangan kejar')."""
    t = tick if tick else tick_size(price)
    return math.ceil(float(price) / t - 1e-9) * t


# Auto Rejection BEI berlaku 19 Okt–31 Des 2026 (sumber: keterangan IDX via
# IDN Financials, 19 Okt 2026). Di luar jendela itu batas historis berbeda dan
# TIDAK dikodekan di sini — lebih baik tidak ada gate daripada gate yang salah.
_ARA_WINDOW = (dt.date(2026, 10, 19), dt.date(2026, 12, 31))


def auto_reject_pct(price: float, day: dt.date | None) -> float | None:
    """Batas ARA (%) untuk hari itu, atau None bila di luar jendela yang diketahui.

    Pita harga: 11–200 → 35%, >200–5.000 → 25%, >5.000 → 20%.
    Harga Rp1–10 memakai batas absolut Rp1 (tidak bisa dinyatakan persen).
    """
    if day is None or not (_ARA_WINDOW[0] <= day <= _ARA_WINDOW[1]):
        return None
    p = float(price)
    if p <= 10:
        return None
    if p <= 200:
        return 35.0
    if p <= 5000:
        return 25.0
    return 20.0


def is_ara_locked(price: float, prev_close: float, day: dt.date | None) -> bool:
    """True bila harga sudah mentok ARA (tidak realistis dibeli di harga itu)."""
    pct = auto_reject_pct(price, day)
    if pct is None or not prev_close or prev_close <= 0:
        return False
    return float(price) >= float(prev_close) * (1.0 + pct / 100.0) * 0.999

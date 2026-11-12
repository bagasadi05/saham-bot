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

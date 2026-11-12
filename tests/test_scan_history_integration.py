"""Integrasi: scan menyimpan histori, lalu menutupnya setelah horizon.

Tidak menyentuh jaringan: frame harga disuntikkan lewat parameter `downloader`.
"""
from __future__ import annotations

import numpy as np

from core import screener, store
from tests.conftest import make_prices


def _frames(bars: int):
    return {f"T{i:02d}": make_prices(np.linspace(1000, 1400, bars))
            for i in range(10)}


def test_scan_appends_history_then_resolves_after_horizon(tmp_store):
    short = _frames(120)
    res = screener.scan(force=True, symbols=list(short),
                        downloader=lambda symbols: short)
    assert res["cached"] is False
    counts = store.scan_history_counts()
    assert counts["total"] == 10
    assert counts["pending"] == 10
    assert store.closed_scan_history("2000-01-01") == []

    # 30 bar kemudian: baris lama sudah lewat horizon 20 hari -> ditutup.
    longer = _frames(150)
    screener.scan(force=True, symbols=list(longer),
                  downloader=lambda symbols: longer)

    closed = store.closed_scan_history("2000-01-01")
    assert len(closed) == 10
    assert all(r["fwd_ret"] is not None or r["outcome"] == "ADJ" for r in closed)
    assert all(r["closed_at"] for r in closed)
    # snapshot hari baru tetap menunggu hasil
    assert store.scan_history_counts()["pending"] == 10

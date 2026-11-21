"""Handler jurnal mengambil close IHSG dari kolom yang benar (huruf kecil)."""
from __future__ import annotations

import asyncio

import pandas as pd

from handlers import jurnal as jh


def test_fetch_ihsg_close_membaca_kolom_lowercase(monkeypatch):
    idx = pd.bdate_range("2026-11-02", periods=3)
    df = pd.DataFrame({"close": [7000.0, 7010.0, 7020.0]}, index=idx)
    monkeypatch.setattr(jh.data, "fetch_history", lambda *a, **k: df)

    out = asyncio.run(jh._fetch_ihsg_close())

    assert out is not None
    assert float(out.iloc[-1]) == 7020.0


def test_fetch_ihsg_close_tanpa_kolom_close(monkeypatch):
    df = pd.DataFrame({"open": [1.0]}, index=pd.bdate_range("2026-11-02", periods=1))
    monkeypatch.setattr(jh.data, "fetch_history", lambda *a, **k: df)

    assert asyncio.run(jh._fetch_ihsg_close()) is None


def test_fetch_ihsg_close_gagal_jaringan(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("yahoo mati")

    monkeypatch.setattr(jh.data, "fetch_history", boom)

    assert asyncio.run(jh._fetch_ihsg_close()) is None

"""Test pembanding emiten & cache konteks IHSG."""
from __future__ import annotations

import numpy as np

from core import analysis, banding
from tests.conftest import make_prices


def _reset_cache():
    banding._mkt_cache.update({"ts": 0.0, "val": None})


def test_compare_row_mengubah_skala_roe_dan_dividen(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    fund = {"per": 12.0, "pbv": 2.0, "roe": 0.2, "div_yield": 0.05}
    row = banding.compare_row("bbca", res, fund)
    assert row["ticker"] == "BBCA"
    assert row["roe"] == 20.0
    assert row["dy"] == 5.0
    assert row["power"] is not None


def test_compare_row_tetap_jalan_tanpa_data():
    row = banding.compare_row("XXXX", None, None)
    assert row["per"] is None and row["power"] is None


def test_pbv_verdict_butuh_minimal_dua_emiten():
    assert banding.pbv_verdict([{"ticker": "A", "pbv": 1.0}]) is None
    teks = banding.pbv_verdict([
        {"ticker": "A", "pbv": 1.0},
        {"ticker": "B", "pbv": 3.0},
    ])
    assert "A" in teks and "B" in teks


def test_market_context_sukses_dicache(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_fetch(*_a, **_k):
        calls["n"] += 1
        return make_prices(np.linspace(6000, 7000, 260))

    monkeypatch.setattr(banding.data, "fetch_history", fake_fetch)
    out1 = banding.market_context()
    out2 = banding.market_context()

    assert out1 is not None and out1["ret20d"] is not None
    assert out1 == out2
    assert calls["n"] == 1  # panggilan kedua dilayani cache


def test_kegagalan_ihsg_hanya_dicache_lima_menit(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_fetch(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(banding.data, "fetch_history", fake_fetch)

    assert banding.market_context() is None
    assert calls["n"] == 2  # dua percobaan seperti kode aslinya
    assert banding.market_context() is None
    assert calls["n"] == 2  # kegagalan baru: tidak spam Yahoo

    # setelah TTL gagal lewat, boleh mencoba lagi
    banding._mkt_cache["ts"] -= banding._MKT_FAIL_TTL + 1
    assert banding.market_context() is None
    assert calls["n"] == 4

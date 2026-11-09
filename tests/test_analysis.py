"""Test indikator, skor, dan struktur hasil analyze()."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from core import analysis
from tests.conftest import make_prices


def test_compute_indicators_memiliki_kolom_wajib(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    for col in ("sma20", "sma50", "sma200", "vol_avg20", "rsi14", "macd",
                "macd_signal", "macd_hist", "high20", "low20", "ret1d",
                "ret20d", "atr14"):
        assert col in ind.columns, col
    assert len(ind) == len(uptrend_df)


def test_uptrend_lebih_tinggi_skornya_daripada_downtrend(uptrend_df, downtrend_df):
    naik = analysis.score_stock(analysis.compute_indicators(uptrend_df))
    turun = analysis.score_stock(analysis.compute_indicators(downtrend_df))
    assert naik["pct"] > 0 > turun["pct"]
    assert naik["rating"] in {"BUY", "STRONG BUY", "NETRAL"}
    assert turun["rating"] in {"SELL", "STRONG SELL", "NETRAL"}


@pytest.mark.parametrize("df_fixture", ["uptrend_df", "downtrend_df"])
def test_skor_selalu_dalam_rentang(df_fixture, request):
    df = request.getfixturevalue(df_fixture)
    sc = analysis.score_stock(analysis.compute_indicators(df))
    assert -100 <= sc["pct"] <= 100
    assert isinstance(sc["bull"], list) and isinstance(sc["bear"], list)
    assert sc["bull"] or sc["bear"]
    assert sc["max"] == 90


def test_rsi_dalam_rentang_0_sampai_100(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    rsi = ind["rsi14"].dropna()
    assert rsi.between(0, 100).all()


def test_snapshot_dan_support_resistance(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    snap = analysis.last_snapshot(ind)
    assert snap["close"] > 0
    assert snap["vol_avg20"] > 0
    sr = analysis.support_resistance(ind)
    assert sr["low_recent"] <= sr["high_recent"]


def test_analyze_menolak_data_kosong():
    with pytest.raises(ValueError):
        analysis.analyze("BBCA", None)
    with pytest.raises(ValueError):
        analysis.analyze("BBCA", pd.DataFrame())


def test_analyze_mengembalikan_kunci_lengkap(uptrend_df):
    res = analysis.analyze("bbca", uptrend_df)
    assert res["ticker"] == "BBCA"
    for key in ("ind", "snap", "score", "sr"):
        assert key in res


def test_extra_confirmations_berupa_teks(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    for baris in analysis.extra_confirmations(ind):
        assert isinstance(baris, str) and baris


def test_skor_volume_nol_tidak_crash():
    df = make_prices([1000.0] * 260, volume=0)
    ind = analysis.compute_indicators(df)
    sc = analysis.score_stock(ind)
    assert math.isfinite(sc["pct"])

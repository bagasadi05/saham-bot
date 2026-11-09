"""Test skor relatif antar-emiten (core/relatif.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from core import relatif


def make_ind(**kw) -> pd.DataFrame:
    """Satu baris indikator sintetis dengan nilai default netral."""
    base = dict(
        close=100.0, sma20=100.0, sma50=100.0, sma200=100.0, high20=100.0,
        volume=1_000_000.0, vol_avg20=1_000_000.0, macd_hist=0.0, rsi14=50.0,
        ret20d=0.0, ret1d=0.0, volat20=0.02, atr14=2.0,
    )
    base.update(kw)
    return pd.DataFrame([base])


GOOD = dict(close=100.0, sma50=120.0, high20=140.0, sma200=100.0,
            rsi14=70.0, ret20d=5.0, macd_hist=1.0, volat20=0.01,
            ret1d=-2.0, volume=2_000_000.0, sma20=130.0, atr14=3.0)
BAD = dict(close=100.0, sma50=80.0, high20=100.0, sma200=100.0,
           rsi14=30.0, ret20d=-5.0, macd_hist=-1.0, volat20=0.05,
           ret1d=3.0, volume=500_000.0, sma20=75.0, atr14=0.5)


def test_factors_from_ind_memuat_semua_bobot():
    f = relatif.factors_from_ind(make_ind())
    assert set(f) == set(relatif.WEIGHTS)
    # close/sma50 - 1 = 0 pada data netral
    assert f["close_sma50"] == pytest.approx(0.0)


def test_faktor_nan_kalau_kolom_hilang():
    df = make_ind().drop(columns=["sma200", "volat20", "atr14"])
    f = relatif.factors_from_ind(df)
    assert pd.isna(f["close_sma200"])
    assert pd.isna(f["volat"])
    assert pd.isna(f["atr"])


def test_universe_kecil_tidak_diberi_peringkat():
    items = [{"ticker": f"T{i}", "ind": make_ind()} for i in range(5)]
    out = relatif.score_universe(items)
    assert all(it["rel_ok"] is False and it["rel_pct"] is None for it in out)


def test_universe_besar_mengurutkan_sesuai_bobot():
    items = ([{"ticker": f"G{i}", "ind": make_ind(**GOOD)} for i in range(5)]
             + [{"ticker": f"B{i}", "ind": make_ind(**BAD)} for i in range(5)])
    out = relatif.score_universe(items)
    assert all(it["rel_ok"] for it in out)
    good = [it["rel_pct"] for it in out if it["ticker"].startswith("G")]
    bad = [it["rel_pct"] for it in out if it["ticker"].startswith("B")]
    assert min(good) > max(bad)
    assert all(0 <= p <= 100 for p in good + bad)
    assert {it["rel_quintil"] for it in out} <= {1, 2, 3, 4, 5}


def test_item_tanpa_dataframe_tidak_bikin_crash():
    items = ([{"ticker": f"T{i}", "ind": make_ind()} for i in range(8)]
             + [{"ticker": "RUSAK", "ind": {"bukan": "dataframe"}}])
    out = relatif.score_universe(items)
    rusak = next(it for it in out if it["ticker"] == "RUSAK")
    assert rusak["rel_ok"] is False


def test_summary_line_jujur_saat_tidak_cukup_data():
    assert "8 emiten" in relatif.summary_line({"rel_ok": False})
    teks = relatif.summary_line({"rel_ok": True, "rel_pct": 87.5, "rel_quintil": 5})
    assert "88/100" in teks and "kuintil 5" in teks

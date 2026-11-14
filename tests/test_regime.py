"""Tes filter rezim pasar IHSG (^JKSE SMA50 vs SMA200) — 100% offline."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import data, regime, reports
from tests.conftest import make_prices


def test_evaluate_ihsg_bull_and_defensive():
    bull_df = make_prices(np.linspace(6000, 7600, 260))
    bull = regime.evaluate_ihsg(bull_df)
    assert bull["status"] == "BULL"
    assert bull["exposure_scale"] == 1.0
    assert bull["sma50"] > bull["sma200"]
    assert bull["bars"] == 260
    assert bull["gap_pct"] > 0

    bear_df = make_prices(np.linspace(7600, 6000, 260))
    bear = regime.evaluate_ihsg(bear_df)
    assert bear["status"] == "DEFENSIVE"
    assert bear["exposure_scale"] == 0.5
    assert bear["sma50"] < bear["sma200"]
    assert bear["gap_pct"] < 0


def test_evaluate_ihsg_honest_na_when_short_or_missing():
    short = regime.evaluate_ihsg(make_prices(np.linspace(6500, 7000, 120)))
    assert short["status"] == "NA"
    assert short["label"] == "tidak tersedia"
    assert short["sma200"] is None
    assert short["exposure_scale"] == 1.0
    assert "120/200" in short["reason"]

    empty = regime.evaluate_ihsg(pd.DataFrame())
    assert empty["status"] == "NA"
    assert empty["exposure_scale"] == 1.0


def test_evaluate_ihsg_is_strictly_causal():
    base = np.linspace(6000, 7500, 240)
    full = make_prices(np.concatenate([base, np.linspace(7500, 4000, 40)]))
    pref = full.iloc[:240]
    r_pref = regime.evaluate_ihsg(pref)
    r_prefix_of_full = regime.evaluate_ihsg(full.iloc[:240])
    assert r_pref["sma50"] == r_prefix_of_full["sma50"]
    assert r_pref["sma200"] == r_prefix_of_full["sma200"]
    assert r_pref["status"] == r_prefix_of_full["status"]


def test_effective_scale_handles_na_and_nan():
    assert regime.effective_scale(None) == 1.0
    assert regime.effective_scale({"status": "BULL", "exposure_scale": 1.0}) == 1.0
    assert regime.effective_scale({"status": "NA", "exposure_scale": 0.0}) == 1.0
    assert regime.effective_scale({"status": "DEFENSIVE", "exposure_scale": 0.5}) == 0.5
    assert regime.effective_scale({"status": "DEFENSIVE", "exposure_scale": float("nan")}) == 0.5


def test_fetch_ihsg_regime_caches_and_retries_fail_offline(monkeypatch):
    calls = {"n": 0}
    df_bear = make_prices(np.linspace(7600, 6200, 250))

    def fake_fetch(ticker, period="2y", raw_symbol=False):
        calls["n"] += 1
        assert ticker == "^JKSE" and raw_symbol is True
        return None if calls["n"] <= 2 else df_bear

    monkeypatch.setattr(data, "fetch_history", fake_fetch)

    na_res = regime.fetch_ihsg_regime()
    assert na_res["status"] == "NA"
    assert calls["n"] == 2  # 2 percobaan awal

    # Di dalam fail TTL -> memakai cache NA tanpa memanggil Yahoo lagi
    na_cached = regime.fetch_ihsg_regime()
    assert na_cached["status"] == "NA"
    assert calls["n"] == 2

    # Simulasikan fail TTL habis -> fetch ulang dan cache hasil valid 6 jam
    regime._regime_cache["ts"] -= regime._FAIL_TTL + 1
    bear_res = regime.fetch_ihsg_regime()
    assert bear_res["status"] == "DEFENSIVE"
    assert calls["n"] == 3
    bear_cached = regime.fetch_ihsg_regime()
    assert bear_cached["status"] == "DEFENSIVE"
    assert calls["n"] == 3


def test_regime_banner_renders_all_states_honestly():
    assert "tidak tersedia" in reports.regime_banner(None)
    assert "konstruktif" in reports.regime_banner(
        {"status": "BULL", "sma50": 7300.0, "sma200": 7000.0, "gap_pct": 4.29}
    )
    def_txt = reports.regime_banner(
        {"status": "DEFENSIVE", "exposure_scale": 0.5,
         "sma50": 6800.0, "sma200": 7200.0, "gap_pct": -5.56}
    )
    assert "defensif" in def_txt.lower() and "50%" in def_txt

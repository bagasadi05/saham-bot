"""Pengujian modul research/train_lgbm.py (fitur interaksi, universe, & segmentasi Syariah)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import syariah
from research import train_lgbm


def test_select_universe_modes():
    lq45 = train_lgbm.select_universe("lq45")
    sy = train_lgbm.select_universe("syariah", max_tickers=40)
    exp = train_lgbm.select_universe("expanded", max_tickers=85)

    assert len(lq45) >= 40
    assert len(sy) >= 20
    assert all(syariah.is_syariah(t) for t in sy)
    assert len(exp) >= len(lq45)
    assert all(t.endswith(".JK") for t in exp)


def test_add_enriched_features_no_nan_explosion():
    dates = pd.date_range("2025-01-02", periods=5, freq="B")
    rows = []
    for d in dates:
        for i, t in enumerate(["TLKM.JK", "BRIS.JK", "BBCA.JK", "ASII.JK"]):
            rows.append({
                "date": d.strftime("%Y-%m-%d"),
                "ticker": t,
                "fwd": 0.01 * (i - 1),
                "f_close_sma50": 0.02 * (i + 1),
                "f_close_sma200": 0.03,
                "f_sma20_sma50": 0.01,
                "f_sma50_sma200": 0.04 if i % 2 == 0 else -0.02,
                "f_macd": 0.005,
                "f_rsi": 0.2 * (i - 1.5),
                "f_breakout": -0.02,
                "f_volume": 0.3 * i,
                "f_mom20": 0.05 * (i - 1),
                "f_mom1": 0.01 * (i - 1),
                "f_volat": 0.2,
                "f_atr": 0.03,
            })
    df = pd.DataFrame(rows)
    enriched = train_lgbm.add_enriched_features(df)
    for col in train_lgbm.ENRICHED_EXTRA:
        assert col in enriched.columns
        assert enriched[col].notna().all()
    assert bool(enriched.loc[enriched["ticker"] == "TLKM.JK", "is_syariah"].iloc[0]) is True
    assert bool(enriched.loc[enriched["ticker"] == "BBCA.JK", "is_syariah"].iloc[0]) is False
    assert np.isfinite(enriched["f_risk_adj_mom"].to_numpy()).all()

"""Pengujian suite riset Colab: regime_eval, quality_combo, dan finetune_sentiment."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import finetune_sentiment, quality_combo, regime_eval


def test_regime_eval_computes_all_4_rules():
    dates = pd.date_range("2024-01-02", periods=120, freq="B")
    rng = np.random.default_rng(42)
    rows = []
    for i, d in enumerate(dates):
        mkt_shock = -0.025 if 50 <= i <= 65 else 0.002
        for j in range(12):
            rows.append({
                "date": d,
                "ticker": f"T{j:02d}.JK",
                "fwd_net": mkt_shock * 5.0 + float(rng.normal(0.0, 0.02)),
                "f_close_sma50": -0.05 if 50 <= i <= 65 else 0.03,
                "f_close_sma200": 0.01,
                "f_sma20_sma50": 0.01,
                "f_sma50_sma200": 0.02,
                "f_macd": 0.003,
                "f_rsi": 0.1 * (j - 6),
                "f_breakout": -0.01,
                "f_volume": 0.1,
                "f_mom20": 0.02 * (j - 6),
                "f_mom1": mkt_shock + float(rng.normal(0.0, 0.005)),
                "f_volat": 0.02,
                "f_atr": 0.02,
            })
    df = pd.DataFrame(rows)
    rep = regime_eval.evaluate_regime_rules(df, horizon=20)
    assert set(rep["rules"]) == {
        "always_100",
        "sma50_200_half",
        "vol_breadth_3tier",
        "adaptive_dd_guard",
    }
    assert rep["recommended_rule_by_calmar"] in rep["rules"]
    assert rep["rules"]["adaptive_dd_guard"]["avg_exposure_pct"] <= 100.0


def test_quality_combo_summarize():
    rows = [
        {"ticker": "TLKM", "is_syariah": True, "quality_ok": True, "ret20d": 5.0, "rel_pct": 90.0},
        {"ticker": "BRIS", "is_syariah": True, "quality_ok": False, "ret20d": -3.0, "rel_pct": 80.0},
        {"ticker": "BBCA", "is_syariah": False, "quality_ok": True, "ret20d": 4.0, "rel_pct": 85.0},
    ]
    summ = quality_combo.summarize_combo(rows)
    assert summ["n"] == 3
    assert summ["n_pass"] == 2
    assert summ["pass_rate_pct"] == 66.7
    assert summ["avg_ret20d_pass"] == 4.5
    assert summ["avg_ret20d_fail"] == -3.0
    assert summ["top_quality_syariah"] == ["TLKM"]


def test_finetune_sentiment_corpus_and_stratified_split():
    corpus = finetune_sentiment.load_corpus()
    assert len(corpus) >= 48
    train, val = finetune_sentiment.stratified_split(corpus, val_ratio=0.25)
    assert len(train) + len(val) == len(corpus)
    assert {lbl for _, lbl in val} == {0, 1, 2}
    assert {lbl for _, lbl in train} == {0, 1, 2}


def test_regime_vol20_3tier_guard():
    from core import regime

    # Bullish SMA50 > SMA200, tapi volatilitas 20h tinggi (>30% ann) -> rem ke 25%
    dates = pd.date_range("2025-01-01", periods=230, freq="B")
    closes = np.linspace(6500.0, 7500.0, len(dates))
    # 25 hari terakhir bergejolak tajam (~2.5% harian => vol20 > 35% ann)
    shocks = np.array([1.0 + (0.028 if i % 2 == 0 else -0.026) for i in range(25)])
    closes[-25:] = closes[-26] * np.cumprod(shocks)
    df = pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes, "volume": 1e9},
        index=dates,
    )
    reg = regime.evaluate_ihsg(df)
    assert reg["status"] == "DEFENSIVE"
    assert reg["exposure_scale"] == 0.25

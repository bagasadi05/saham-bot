"""Tes regresi Fase 3 PLAN-PERBAIKAN-AUDIT.md (F-12 monotonisitas & F-15 sensitivitas slippage)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import backtest_real
from core import relatif


def test_f12_quintile_monotonicity_jujur():
    # Data produksi Q1..Q5 tidak monoton sempurna (Q2 < Q1)
    res = relatif.check_quintile_monotonicity()
    assert res["n_quintiles"] == 5
    assert res["monotonic"] is False
    assert res["spearman_rho"] is not None

    mono = relatif.check_quintile_monotonicity({
        1: {"excess_ret_pct": -0.5},
        2: {"excess_ret_pct": -0.1},
        3: {"excess_ret_pct": 0.1},
        4: {"excess_ret_pct": 0.3},
        5: {"excess_ret_pct": 0.6},
    })
    assert mono["monotonic"] is True
    assert mono["spearman_rho"] == 1.0


def test_f15_slippage_sensitivity_menurunkan_return():
    rng = np.random.default_rng(7)
    n = 300
    closes = 2000.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    idx = pd.bdate_range("2025-01-01", periods=n)
    sig = np.zeros(n, dtype=bool)
    sig[30::15] = True
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.02,
            "Low": closes * 0.98,
            "Close": closes,
            "Volume": np.full(n, 2_000_000.0),
            "signal": sig,
            "atr": closes * 0.02,
        },
        index=idx,
    )
    rows = backtest_real.slippage_sensitivity(df, spreads=(0.0, 0.001, 0.003))
    assert [r["spread_pct"] for r in rows] == [0.0, 0.1, 0.3]
    assert rows[0]["trades"] > 0
    assert rows[2]["return_pct"] <= rows[0]["return_pct"]

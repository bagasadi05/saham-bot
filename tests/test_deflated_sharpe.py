"""C4: Deflated Sharpe Ratio untuk 20 fase (multiple testing)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import portfolio


def _nets(n: int = 10, size: int = 30, seed: int = 0):
    rng = np.random.default_rng(seed)
    return [rng.normal(0.01, 0.02, size) for _ in range(n)]


def test_dsr_in_unit_interval_and_best_matches_max_sharpe():
    nets = _nets()
    res = portfolio.deflated_sharpe(nets)

    assert 0.0 <= res["dsr"] <= 1.0
    expected_best = max(portfolio.sharpe_ratio(x) for x in nets)
    assert abs(res["best_sr"] - round(expected_best, 3)) < 1e-3
    assert res["n_trials"] == len(nets)


def test_more_trials_lower_dsr():
    nets = _nets()
    few = portfolio.deflated_sharpe(nets, n_trials=5)
    many = portfolio.deflated_sharpe(nets, n_trials=50)

    assert many["sr0"] >= few["sr0"]
    assert many["dsr"] <= few["dsr"]


def test_identical_phases_have_no_deflation():
    nets = [np.full(30, 0.01) + np.linspace(-0.001, 0.001, 30)] * 4
    res = portfolio.deflated_sharpe(nets)
    assert res["sr0"] == 0.0
    assert res["dsr"] is not None and res["dsr"] > 0.5


def test_not_enough_phases_returns_none():
    res = portfolio.deflated_sharpe([np.array([0.01, 0.02, 0.03])])
    assert res["dsr"] is None


def test_run_payload_includes_dsr():
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2024-01-01", periods=40)
    rows = []
    for d in dates:
        for i in range(10):
            rows.append({"date": d, "ticker": f"T{i}",
                         "pred": float(rng.normal()), "fwd": float(rng.normal(0, 0.01)),
                         "decile": i % 5})
    oos = pd.DataFrame(rows)

    res = portfolio.run(oos, q=0.2, step=20)

    assert "deflated_sharpe" in res
    assert set(res["deflated_sharpe"]) == {"n_trials", "best_sr", "sr0", "dsr"}

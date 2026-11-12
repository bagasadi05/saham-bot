"""B1: t-stat HAC tidak boleh lebih optimistis daripada t naif pada IC overlap."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import evaluate


def _ar1(n: int = 400, phi: float = 0.8, shift: float = 0.5, seed: int = 0):
    rng = np.random.default_rng(seed)
    eps = rng.normal(size=n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + eps[i]
    return x + shift


def _naive_t(x: np.ndarray) -> float:
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x)))


def test_hac_less_optimistic_for_autocorrelated_series():
    x = _ar1()
    naive = _naive_t(x)
    hac = evaluate.newey_west_t(x, 20)

    assert hac is not None
    assert naive > 2.0              # data uji memang "signifikan" secara naif
    assert abs(hac) < abs(naive) * 0.7


def test_hac_close_to_naive_for_iid_series():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 500)
    naive = _naive_t(x)
    hac = evaluate.newey_west_t(x, 20)

    assert hac is not None
    assert abs(hac - naive) < 0.5


def test_ic_summary_keeps_both_t_and_t_hac():
    x = _ar1()
    s = evaluate.ic_summary(pd.Series(x), nlags=20)

    assert s["t"] is not None
    assert s["t_hac"] is not None
    assert abs(s["t_hac"]) < abs(s["t"])
    assert s["n_dates"] == len(x)


def test_ic_summary_empty_has_no_hac():
    s = evaluate.ic_summary(pd.Series([], dtype=float), nlags=20)
    assert s["t"] is None and s["t_hac"] is None and s["n_dates"] == 0


def test_ic_summary_without_nlags_skips_hac():
    s = evaluate.ic_summary(pd.Series(_ar1()))
    assert s["t_hac"] is None

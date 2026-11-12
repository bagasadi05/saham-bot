"""Tes regularisasi ridge berbasis CV deret waktu (research/evaluate.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research import evaluate


def _frame(n_dates: int = 120, n_tickers: int = 12, seed: int = 0) -> pd.DataFrame:
    """Panel sintetis: f_good benar-benar memprediksi fwd, f_noise tidak."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.bdate_range("2018-01-01", periods=n_dates):
        noise = rng.normal(0, 1, n_tickers)
        good = rng.normal(0, 1, n_tickers)
        fwd = 0.02 * good + rng.normal(0, 0.02, n_tickers)
        for i in range(n_tickers):
            rows.append({"date": d, "ticker": f"T{i:02d}",
                         "f_good": float(good[i]),
                         "f_noise": float(noise[i]),
                         "score": float(0.1 * good[i]),
                         "fwd": float(fwd[i])})
    return pd.DataFrame(rows)


def test_ridge_fit_tanpa_penalti_setara_ols():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(200), rng.normal(size=(200, 3))])
    y = X @ np.array([0.5, 1.0, -2.0, 0.3]) + rng.normal(0, 0.01, 200)
    w = evaluate.ridge_fit(X, y, 0.0)
    ols = np.linalg.lstsq(X, y, rcond=None)[0]
    assert np.allclose(w, ols, atol=1e-8)


def test_ridge_fit_tidak_menghukum_intersep():
    """lam besar: bobot fitur menyusut, intersep tetap dekat rata-rata target."""
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(500), rng.normal(size=(500, 2))])
    y = np.full(500, 3.0) + rng.normal(0, 0.01, 500)
    w = evaluate.ridge_fit(X, y, 10.0)
    assert w[0] == pytest.approx(3.0, abs=0.02)
    assert abs(w[1]) < 0.01 and abs(w[2]) < 0.01


def test_choose_lam_cv_data_pendek_memakai_default():
    cv = evaluate.choose_lam_cv(_frame(n_dates=10, n_tickers=5), ["f_good"], 20)
    assert cv["lam_rel"] == 0.1
    assert cv["folds"] == 0
    assert "note" in cv


def test_choose_lam_cv_memilih_dari_grid_dan_melaporkan_semua_kandidat():
    df = _frame()
    df["target"] = evaluate.rank_target(df)
    cv = evaluate.choose_lam_cv(df, ["f_good", "f_noise"], horizon=20)
    assert cv["lam_rel"] in evaluate.LAM_REL_GRID
    assert cv["folds"] >= 1
    assert set(cv["cv_ic"]) <= {f"{g:g}" for g in evaluate.LAM_REL_GRID}


def test_walk_forward_memakai_lam_hasil_cv_dan_baseline_eq():
    df = _frame(n_dates=800, n_tickers=15, seed=3)
    wf, deciles, oos = evaluate.walk_forward(
        df, ["f_good", "f_noise"], horizon=20, test_years=[2020])
    ok = [r for r in wf if not r.get("skipped")]
    assert ok, "fold 2020 harus terisi"
    r = ok[0]
    assert r["lam_rel"] in evaluate.LAM_REL_GRID
    assert r["ic_eq"] is not None
    assert r["ic_model"] is not None
    assert "lam_cv" in r
    assert oos is not None and len(oos) > 0

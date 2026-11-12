"""Tes CI bootstrap alpha neto & pembanding IHSG (research/portfolio.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research import portfolio


def test_bootstrap_seri_konstan_menyempit_ke_nilai_itu():
    ci = portfolio.moving_block_bootstrap_ci([0.5] * 20, iters=200, block=4)
    assert ci["n"] == 20
    assert ci["lo"] == pytest.approx(0.5, abs=1e-9)
    assert ci["hi"] == pytest.approx(0.5, abs=1e-9)
    assert ci["p_positive"] == 1.0


def test_bootstrap_terlalu_pendek_tidak_mengarang_ci():
    ci = portfolio.moving_block_bootstrap_ci([0.1, 0.2], iters=100)
    assert ci["lo"] is None and ci["hi"] is None
    assert ci["p_positive"] is None


def test_bootstrap_blok_dilebihkan_dari_jumlah_nilai():
    ci = portfolio.moving_block_bootstrap_ci([1.0, 2.0, 3.0], iters=50, block=99)
    assert ci["block"] == 3
    assert ci["lo"] is not None


def test_bootstrap_blok_penuh_setara_sampel_utuh():
    """block=n berarti resample selalu seluruh deret -> CI tepat di rata-rata.

    Ini membuktikan sampel diambil sebagai blok bersambung, bukan diacak
    per nilai (kalau diacak, lebar CI tidak akan nol).
    """
    x = [-1.0, 1.0, -1.0, 1.0, 3.0, -2.0]
    ci = portfolio.moving_block_bootstrap_ci(x, iters=300, block=len(x))
    assert ci["lo"] == pytest.approx(np.mean(x), abs=1e-3)
    assert ci["hi"] == pytest.approx(np.mean(x), abs=1e-3)


def test_bootstrap_seed_sama_hasil_sama():
    x = [0.1, -0.2, 0.3, -0.1, 0.05, 0.2, -0.15, 0.4]
    a = portfolio.moving_block_bootstrap_ci(x, iters=500, block=3, seed=7)
    b = portfolio.moving_block_bootstrap_ci(x, iters=500, block=3, seed=7)
    assert a == b


def test_ihsg_period_returns_menggeser_sebanyak_step(monkeypatch):
    idx = pd.bdate_range("2024-01-01", periods=10)
    close = np.arange(100.0, 110.0)
    df = pd.DataFrame({"close": close}, index=idx)
    monkeypatch.setattr(portfolio.data, "fetch_history",
                        lambda *a, **k: df)
    ret = portfolio.ihsg_period_returns(step=2)
    assert len(ret) == 8
    assert ret.iloc[0] == pytest.approx(close[2] / close[0] - 1.0)
    assert ret.index[0] == idx[0]


def test_ihsg_kosong_kalau_data_tidak_ada(monkeypatch):
    monkeypatch.setattr(portfolio.data, "fetch_history", lambda *a, **k: None)
    assert portfolio.ihsg_period_returns(step=20).empty


def _oos(n_dates=60, n_tickers=10) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    rows = []
    for d in pd.bdate_range("2024-01-01", periods=n_dates):
        fwd = rng.normal(0.0, 0.05, n_tickers)
        pred = fwd + rng.normal(0.0, 0.01, n_tickers)
        for i in range(n_tickers):
            rows.append({"date": d, "ticker": f"T{i:02d}",
                         "pred": float(pred[i]), "fwd": float(fwd[i])})
    df = pd.DataFrame(rows)
    df["decile"] = df.groupby("date")["pred"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False,
                          duplicates="drop"))
    return df


def test_one_phase_mencatat_return_ihsg_dan_alpha_vs_ihsg():
    oos = _oos()
    dates = sorted(oos["date"].unique())
    # IHSG +1% untuk setiap tanggal periode -> mudah diperiksa
    ihsg = pd.Series(0.01, index=pd.to_datetime(dates))
    res = portfolio.run(oos, q=0.2, step=20, hold=0.4, ihsg=ihsg)
    assert res["across_phases"]["alpha_vs_ihsg_mean_pct"] is not None
    assert res["across_phases"]["n_phases_vs_ihsg"] == res["n_phases"]
    phase = res["phases"][0]
    assert phase["alpha_vs_ihsg"]["n"] > 0
    # alpha vs IHSG = neto - 1%
    net = phase["net"]["mean_pct"]
    vs = phase["alpha_vs_ihsg"]["mean_pct"]
    assert vs == pytest.approx(net - 1.0, abs=0.05)


def test_run_tanpa_ihsg_tidak_mengarang_pembanding():
    oos = _oos()
    res = portfolio.run(oos, q=0.2, step=20)
    assert res["across_phases"]["alpha_vs_ihsg_mean_pct"] is None
    assert res["phases"][0]["alpha_vs_ihsg"]["n"] == 0
    assert res["ci95_alpha_net"]["lo"] is not None

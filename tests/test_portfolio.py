"""Test harness portofolio (research/portfolio.py) — logika biaya & fase."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research import portfolio


def make_oos(n_dates: int = 60, n_tickers: int = 10) -> pd.DataFrame:
    """Prediksi sintetis: pred tinggi benar-benar diikuti fwd tinggi."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        fwd = rng.normal(0.0, 0.05, n_tickers)
        pred = fwd + rng.normal(0.0, 0.01, n_tickers)
        for i in range(n_tickers):
            rows.append({
                "date": d, "ticker": f"T{i:02d}",
                "pred": float(pred[i]), "fwd": float(fwd[i]),
            })
    df = pd.DataFrame(rows)
    # kolom decile seperti hasil evaluate.py
    df["decile"] = df.groupby("date")["pred"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False,
                          duplicates="drop"))
    return df


def test_summarize_menghitung_t_dan_kumulatif():
    s = portfolio.summarize(np.array([0.01, 0.02, -0.005]))
    assert s["n"] == 3
    assert s["mean_pct"] == pytest.approx((0.01 + 0.02 - 0.005) / 3 * 100, abs=1e-3)
    assert s["cum_pct"] == pytest.approx((1.01 * 1.02 * 0.995 - 1) * 100, abs=1e-2)
    assert s["t"] is not None


def test_summarize_kosong_tidak_crash():
    assert portfolio.summarize(np.array([]))["n"] == 0


def test_one_phase_rebalance_non_overlapping():
    oos = make_oos(n_dates=60, n_tickers=10)
    df = portfolio.one_phase(oos, q=0.2, step=20, offset=0)
    # 60 tanggal → 3 periode (0, 20, 40)
    assert len(df) == 3
    assert df["n"].eq(10).all()
    # periode pertama: beli penuh (belum punya posisi), tanpa penjualan
    assert df["turnover_two_way"].iloc[0] == pytest.approx(1.0)
    assert df["turnover"].iloc[0] == pytest.approx(0.5)
    assert df["cost"].iloc[0] == pytest.approx(portfolio.BUY_COST)


def test_biaya_hanya_pada_bagian_yang_berganti():
    oos = make_oos()
    df = portfolio.one_phase(oos, q=0.2, step=20, offset=0)
    for r in df.itertuples():
        expected = r.buy * portfolio.BUY_COST + r.sell * portfolio.SELL_COST
        assert r.cost == pytest.approx(expected)
        assert r.net == pytest.approx(r.gross - r.cost)
        assert r.turnover_two_way == pytest.approx(r.buy + r.sell)


def test_kuintil_teratas_memang_prediksi_tertinggi():
    oos = make_oos(n_dates=20, n_tickers=10)
    d = oos["date"].iloc[0]
    g = oos[oos["date"] == d].sort_values("pred", ascending=False)
    # 20% dari 10 emiten = 2 prediksi teratas
    df = portfolio.one_phase(oos, q=0.2, step=20, offset=0)
    # gross periode pertama = rata-rata fwd dua prediksi teratas
    assert df["gross"].iloc[0] == pytest.approx(
        g.head(2)["fwd"].mean())


def test_run_menguji_semua_fase_bukan_satu_fase():
    oos = make_oos(n_dates=80, n_tickers=10)
    res = portfolio.run(oos, q=0.2, step=20)
    assert res["n_phases"] == 20
    assert len(res["phases"]) == 20
    a = res["across_phases"]
    assert a["alpha_gross_min_pct"] <= a["alpha_gross_mean_pct"] <= a["alpha_gross_max_pct"]
    assert 0 <= a["phases_net_positive"] <= 20
    # cross-check semua tanggal harus searah dengan rata-rata antar-fase
    assert res["all_dates_q5_excess"]["n"] == 80

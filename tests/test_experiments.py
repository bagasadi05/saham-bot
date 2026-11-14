"""Tes offline untuk harness ablasi hipotesis (`research/experiments.py`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research import experiments


def _synthetic_oos(n_dates: int = 80, n_tickers: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        fwd = rng.normal(0.002, 0.04, n_tickers)
        pred = fwd + rng.normal(0.0, 0.015, n_tickers)
        for i in range(n_tickers):
            rows.append({
                "date": d, "ticker": f"T{i:02d}",
                "pred": float(pred[i]), "fwd": float(fwd[i]),
                "volat20": 0.015 + 0.002 * i,
            })
    return pd.DataFrame(rows)


def _synthetic_ihsg(start: str = "2023-01-01", periods: int = 420) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=periods)
    # Naik lalu turun tajam agar melintasi SMA50/SMA200
    close = np.concatenate([
        np.linspace(6500.0, 7400.0, periods // 2),
        np.linspace(7400.0, 6200.0, periods - periods // 2),
    ])
    return pd.DataFrame({"close": close}, index=idx)


def test_min_hold_menahan_saham_selama_durasi_minimum():
    g = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(1, 11)],
        "pred": [float(i) for i in range(1, 11)],
    })
    # T1 peringkat paling bawah, tapi age=1 < min_hold=2 sehingga tetap terkunci
    picked = experiments.select_names(
        g, prev_age={"T1": 1}, take_frac=0.2, hold_frac=0.4, min_hold=2
    )
    assert "T1" in picked
    assert len(picked) == 2

    # Setelah age >= min_hold, T1 tersingkir
    released = experiments.select_names(
        g, prev_age={"T1": 2}, take_frac=0.2, hold_frac=0.4, min_hold=2
    )
    assert "T1" not in released


def test_ihsg_regime_frame_kausal_dan_exposure_modes():
    reg = experiments.ihsg_regime_frame(_synthetic_ihsg(), step=20)
    assert {"sma50", "sma200", "vol20", "ihsg_fwd"}.issubset(reg.columns)

    bear_row = reg.iloc[-1]
    assert bear_row["close"] < bear_row["sma200"]
    assert experiments.exposure_for_row(bear_row, "none") == 1.0
    assert experiments.exposure_for_row(bear_row, "sma200_cash") == 0.0
    assert experiments.exposure_for_row(bear_row, "sma200_half") == 0.5
    assert experiments.exposure_for_row(bear_row, "sma50_200_half") == 0.5
    assert 0.0 < experiments.exposure_for_row(bear_row, "vol_target_14") <= 1.0

    with pytest.raises(ValueError, match="regime"):
        experiments.exposure_for_row(bear_row, "invalid_mode")


def test_smoothed_scores_strictly_causal():
    oos = _synthetic_oos(n_dates=10, n_tickers=8)
    s1 = experiments.add_smoothed_scores(oos)
    # Mengubah nilai masa depan tidak boleh memengaruhi pred_ema5 di tanggal awal
    oos_future_changed = oos.copy()
    last_date = oos_future_changed["date"].max()
    oos_future_changed.loc[oos_future_changed["date"] == last_date, "pred"] = 999.0
    s2 = experiments.add_smoothed_scores(oos_future_changed)

    first_date = s1["date"].min()
    a = s1[s1["date"] == first_date].sort_values("ticker")["pred_ema5"].to_numpy()
    b = s2[s2["date"] == first_date].sort_values("ticker")["pred_ema5"].to_numpy()
    assert np.allclose(a, b)


def test_slippage_dan_multiple_testing_dsr_tercatat_jujur():
    oos = _synthetic_oos(n_dates=100, n_tickers=10)
    reg = experiments.ihsg_regime_frame(_synthetic_ihsg(periods=450), step=20)
    base = experiments.simulate_variant(oos, reg, name="base", extra_rt_bps=0.0)
    slip = experiments.simulate_variant(oos, reg, name="slip", extra_rt_bps=40.0)
    assert slip["alpha_net_pct"] < base["alpha_net_pct"]

    specs = (
        {"name": "base", "q": 0.2, "hold": 0.4},
        {"name": "h60", "q": 0.2, "hold": 0.6},
        {"name": "reg", "q": 0.2, "hold": 0.5, "regime": "sma50_200_half"},
    )
    rep = experiments.run_ablation(oos, reg, specs=specs, step=20)
    assert rep["n_configs"] == 3
    assert rep["total_trials"] == 60
    assert all(v["n_trials_all"] == 60 for v in rep["variants"])
    assert all(v["dsr_all"] <= v["dsr_20"] + 1e-3 for v in rep["variants"]
               if v["dsr_all"] is not None and v["dsr_20"] is not None)
    md = experiments.render_md(rep)
    assert "BELOW GATE" in md or "PASS" in md


def test_build_forward_dataset_and_horizon_comparison():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2021-01-04", periods=320)
    rows = []
    for t_idx in range(10):
        px = 1000.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.015, size=len(dates)))
        for i, d in enumerate(dates):
            rows.append({
                "date": d,
                "ticker": f"T{t_idx:02d}.JK",
                "close": float(px[i]),
                "score": 0.0,
                "f_mom": rng.normal(),
                "f_volat": 0.02,
            })
    ds = pd.DataFrame(rows)
    d5, feats = experiments.build_forward_dataset(ds, 5)
    assert feats == ["f_mom", "f_volat"]
    t0 = d5[d5["ticker"] == "T00.JK"].sort_values("date")
    t0_orig = ds[ds["ticker"] == "T00.JK"].sort_values("date")
    assert len(t0) == 315
    expected_first = float(t0_orig["close"].iloc[5] / t0_orig["close"].iloc[0] - 1.0)
    assert abs(float(t0["fwd"].iloc[0]) - expected_first) < 1e-12

    hz = experiments.run_horizon_comparison(
        ds, _synthetic_ihsg(periods=350), horizons=(5, 10), years=(2022,)
    )
    assert [r["horizon"] for r in hz] == [5, 10]
    assert all("variants" in r for r in hz)
    md = experiments.render_md({
        "n_configs": 0, "total_trials": 0, "dsr_gate": 0.95,
        "variants": [], "slippage_sensitivity": [], "horizon_comparison": hz,
    })
    assert "Perbandingan Horizon Walk-Forward" in md
    assert "5d" in md and "10d" in md

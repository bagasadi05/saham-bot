"""C1-C2: turnover berbobot dan hysteresis pada simulasi portofolio."""
from __future__ import annotations

import pandas as pd

from research import portfolio


def test_initial_buy_charges_buy_fee_only():
    tc = portfolio.turnover_cost({}, {"A": 1.0})
    assert tc["two_way"] == 1.0
    assert tc["turnover"] == 0.5
    assert tc["cost"] == portfolio.BUY_COST


def test_full_swap_costs_buy_plus_sell():
    tc = portfolio.turnover_cost({"A": 1.0}, {"B": 1.0})
    assert tc["two_way"] == 2.0
    assert tc["turnover"] == 1.0
    assert tc["cost"] == portfolio.BUY_COST + portfolio.SELL_COST


def test_identical_portfolio_has_zero_turnover():
    tc = portfolio.turnover_cost({"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5})
    assert tc["two_way"] == 0.0
    assert tc["cost"] == 0.0


def test_partial_weight_change_is_linear():
    prev = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    cur = {"A": 0.25, "B": 0.25, "C": 0.25, "E": 0.25}
    tc = portfolio.turnover_cost(prev, cur)
    assert tc["two_way"] == 0.5
    assert tc["turnover"] == 0.25
    assert tc["cost"] == 0.25 * (portfolio.BUY_COST + portfolio.SELL_COST)


def _rank_frame():
    return pd.DataFrame({
        "ticker": [f"T{i}" for i in range(1, 11)],
        "pred": [float(i) for i in range(1, 11)],
        "fwd": [0.0] * 10,
    })


def test_hysteresis_keeps_incumbent_inside_hold_band():
    g = _rank_frame()
    prev = {"T7": 0.1}          # peringkat persentil 0,7

    assert "T7" not in portfolio._select(g, prev, 0.2, 0.2)
    assert "T7" in portfolio._select(g, prev, 0.2, 0.4)


def _two_date_oos():
    d1, d2 = pd.Timestamp("2026-01-05"), pd.Timestamp("2026-02-02")
    rows = []
    for i in range(1, 7):
        rows.append({"date": d1, "ticker": f"T{i}", "pred": float(i),
                     "fwd": float(i) / 100, "decile": 0})
    for i in range(1, 7):
        rows.append({"date": d2, "ticker": f"T{i}", "pred": float(7 - i),
                     "fwd": float(7 - i) / 100, "decile": 0})
    return pd.DataFrame(rows)


def test_one_phase_full_swap_costs_round_trip():
    df = portfolio.one_phase(_two_date_oos(), 1 / 3, 1, 0)
    assert len(df) == 2
    assert df.iloc[0]["cost"] == portfolio.BUY_COST      # beli awal
    assert df.iloc[1]["turnover_two_way"] == 2.0          # tukar 2 dari 2 nama
    assert abs(df.iloc[1]["cost"] - (portfolio.BUY_COST + portfolio.SELL_COST)) < 1e-12


def test_run_records_hysteresis_setting():
    oos = _two_date_oos()
    base = portfolio.run(oos, 1 / 3, 1)
    assert base["hysteresis"] is False
    assert base["hold_quintile"] == 1 / 3
    assert base["avg_turnover_two_way_pct"] >= 0

    hyst = portfolio.run(oos, 1 / 3, 1, 0.6)
    assert hyst["hysteresis"] is True
    assert hyst["hold_quintile"] == 0.6


def test_min_hold_locks_incumbent_and_regime_scales_exposure():
    import numpy as np

    from core import regime as regime_mod
    from tests.conftest import make_prices

    oos = _two_date_oos()
    # min_hold=2 mengunci pemegang hari ke-1 (T1, T2) meskipun T6 & T5 naik ke atas pada hari ke-2
    df_lock = portfolio.one_phase(oos, 1 / 3, 1, 0, hold=0.4, min_hold=2)
    assert df_lock.iloc[1]["turnover"] == 0.0

    # Regime defensif pada hari-2 memangkas eksposur 100% -> 50% dan menurunkan alpha vs full bench
    ihsg_raw = make_prices(np.linspace(7600, 6200, 260), start="2023-01-02")
    reg_df = regime_mod.ihsg_indicators(ihsg_raw)
    d_bull = pd.Timestamp(oos["date"].iloc[0])
    d_bear = pd.Timestamp(oos["date"].iloc[-1])
    reg_df = reg_df.iloc[-2:].copy()
    reg_df.index = pd.DatetimeIndex([d_bull, d_bear])
    reg_df.loc[d_bull, ["sma50", "sma200"]] = [7200.0, 7000.0]
    reg_df.loc[d_bear, ["sma50", "sma200"]] = [6800.0, 7000.0]

    res = portfolio.run(
        oos, 1 / 3, 1, hold=0.4, min_hold=2,
        regime_mode="sma50_200_half", regime_df=reg_df,
    )
    assert res["min_hold"] == 2
    assert res["regime"] == "sma50_200_half"
    assert res["across_phases"]["avg_exposure_pct"] == 75.0
    assert res["across_phases"]["alpha_active_gross_mean_pct"] is not None

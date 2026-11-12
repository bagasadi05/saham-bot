"""/validasi menampilkan hysteresis dan Deflated Sharpe bila artefak memuatnya."""
from __future__ import annotations

import bot


def _payload(portfolio):
    return {
        "meta": {"start": "2017-09-07", "end": "2026-10-09",
                 "n_tickers": 44, "n_rows": 100_000, "horizon": 20,
                 "universe": "LQ45_2026"},
        "score": {
            "ic": {"mean_ic": 0.0115, "t": 2.68, "t_hac": 0.85},
            "ic_timeseries": {"mean_ic": -0.0207, "t": -2.33},
        },
        "ratings": [], "walk_forward": [], "quintiles": [],
        "portfolio": portfolio,
    }


def test_hysteresis_and_dsr_are_rendered():
    txt = bot.format_validasi(_payload({
        "n_phases": 20, "holding_days": 20, "cost_per_round_trip_pct": 0.4,
        "avg_turnover_pct": 50.9, "avg_turnover_two_way_pct": 101.7,
        "quintile": 0.2, "hold_quintile": 0.4, "hysteresis": True,
        "deflated_sharpe": {"n_trials": 20, "best_sr": 0.242,
                            "sr0": 0.117, "dsr": 0.837},
        "alpha_gross_mean_pct": 0.535, "alpha_net_mean_pct": 0.332,
        "alpha_net_median_pct": 0.309, "phases_net_positive": 15,
    }))

    assert "hysteresis" in txt
    assert "tahan sampai top 40%" in txt
    assert "Deflated Sharpe 0.837" in txt


def test_portfolio_lama_tanpa_field_baru_tetap_jalan():
    txt = bot.format_validasi(_payload({
        "n_phases": 20, "holding_days": 20, "cost_per_round_trip_pct": 0.5,
        "avg_turnover_pct": 73.6, "alpha_gross_mean_pct": 0.443,
        "alpha_net_mean_pct": 0.076, "alpha_net_median_pct": -0.05,
        "phases_net_positive": 9,
    }))

    assert "hysteresis" not in txt
    assert "Deflated Sharpe" not in txt
    assert "neto +0.08%" in txt

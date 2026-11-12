"""/validasi menampilkan baseline bobot-sama, CI alpha neto, dan pembanding IHSG."""
from __future__ import annotations

import bot


def _payload(**kw):
    p = {
        "meta": {"start": "2017-09-07", "end": "2026-10-09",
                 "n_tickers": 45, "n_rows": 91_395, "horizon": 20,
                 "universe": "LQ45_2026"},
        "score": {"ic": {"mean_ic": 0.0115, "t": 2.68, "t_hac": 0.85},
                  "ic_timeseries": {"mean_ic": -0.0207, "t": -2.33}},
        "ratings": [], "quintiles": [],
    }
    p.update(kw)
    return p


def test_walk_forward_menampilkan_baseline_bobot_sama():
    txt = bot.format_validasi(_payload(
        walk_forward=[{"year": 2024, "ic_score": 0.02, "ic_eq": 0.031,
                       "ic_model": 0.048, "ic_blend": 0.052, "lam_rel": 0.1}],
        walk_forward_summary={"n_years": 5, "ic_model_mean": 0.034,
                              "ic_eq_mean": 0.021, "years_model_beats_eq": 3},
    ))
    assert "bobot sama +0.031" in txt
    assert "[lam 0.1]" in txt
    assert "model +0.034 vs bobot sama +0.021" in txt
    assert "menang 3/5 tahun" in txt


def test_walk_forward_lama_tanpa_ic_eq_tetap_jalan():
    txt = bot.format_validasi(_payload(
        walk_forward=[{"year": 2024, "ic_score": 0.02, "ic_model": 0.048,
                       "ic_blend": 0.052}],
    ))
    assert "2024: skor lama +0.020" in txt
    assert "bobot sama" not in txt


def test_portofolio_menampilkan_ci_dan_pembanding_ihsg():
    txt = bot.format_validasi(_payload(portfolio={
        "n_phases": 20, "holding_days": 20, "cost_per_round_trip_pct": 0.4,
        "avg_turnover_pct": 50.9, "quintile": 0.2, "hold_quintile": 0.4,
        "hysteresis": True, "alpha_gross_mean_pct": 0.535,
        "alpha_net_mean_pct": 0.332, "alpha_net_median_pct": 0.309,
        "phases_net_positive": 15,
        "ci95_alpha_net": {"n": 20, "block": 4, "iters": 2000,
                           "lo": 0.207, "hi": 0.5, "p_positive": 1.0},
        "alpha_vs_ihsg_mean_pct": 1.193, "phases_vs_ihsg_positive": 20,
    }))
    assert "CI 95% alpha neto +0.21% s.d. +0.50%" in txt
    assert "vs IHSG +1.19%/periode (20/20 fase)" in txt
    assert "bukan alpha murni" in txt

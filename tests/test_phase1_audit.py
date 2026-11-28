"""Tes offline untuk research/phase1_audit.py (biaya BEI, filter ADTV, kapasitas & benchmark naif)."""
from __future__ import annotations

import numpy as np

from research import phase1_audit
from tests.conftest import make_prices


def test_phase1_audit_benchmark_comparison_offline():
    rng = np.random.default_rng(7)
    frames = {}
    for idx, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]):
        drift = 0.0005 + idx * 0.0004
        rets = rng.normal(drift, 0.015, size=220)
        closes = 1000.0 * np.cumprod(1.0 + rets)
        # Variasikan likuiditas agar filter ADTV >= 2M dan >= 5M teruji
        vol = (1_000_000 if idx < 2 else 8_000_000)
        frames[sym] = make_prices(closes, start="2024-01-01", volume=vol)

    res = phase1_audit.run_benchmark_comparison(
        frames, horizon=20, step=20, hold=0.6, adtv_thresholds=(0.0, 2e9, 5e9),
    )
    assert res["ok"] is True
    assert res["n_tickers"] == 8
    for bucket in ("all_universe", "adtv_ge_2m", "adtv_ge_5m"):
        b = res["benchmarks"][bucket]
        assert set(b.keys()) == {"model_12f", "bench_mom_12_1", "bench_low_vol", "bench_random"}
        m = b["model_12f"]
        assert m["ok"] is True
        assert m["n_phases"] > 0
        assert m["alpha_net_mean_pct"] <= m["alpha_gross_mean_pct"]
        assert m["max_pos_capacity_5pct_adtv_rp"] > 0
        assert m["max_pos_capacity_10pct_adtv_rp"] == round(m["median_q5_adtv_rp"] * 0.10)

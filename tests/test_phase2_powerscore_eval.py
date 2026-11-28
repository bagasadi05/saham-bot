"""Tes offline Fase 2: evaluate_powerscore, powerscore validation badge, & scorecard quintile breakdown."""
from __future__ import annotations

import numpy as np

from core import powerscore, scorecard
from research import evaluate_powerscore
from tests.conftest import make_prices


def test_powerscore_validation_status_and_rel_pct_integration():
    # Default: heuristik murni
    pw_h = powerscore.combine(40.0, 30.0, 20.0)
    assert pw_h["validation_status"] == "HEURISTIK"
    assert "Heuristik" in pw_h["validation_badge"]
    assert pw_h["components"]["rel_pct"] is None

    # Dengan rel_pct tervalidasi OOS -> status TERVALIDASI_SEBAGIAN
    pw_v = powerscore.combine(40.0, 30.0, 20.0, rel_pct=90.0)
    assert pw_v["validation_status"] == "TERVALIDASI_SEBAGIAN"
    assert "Tervalidasi OOS" in pw_v["validation_badge"]
    assert pw_v["components"]["rel_pct"] == 90.0
    assert pw_v["pct"] > pw_h["pct"]


def test_scorecard_quintile_breakdown_and_ci():
    rows = []
    for i in range(15):
        rows.append({
            "trade_date": f"2026-10-{i + 1:02d}",
            "ticker": f"T{i}",
            "signal": "BELI" if i >= 8 else "TAHAN",
            "rel_pct": 85.0 if i >= 8 else 15.0,
            "fwd_ret": 4.0 if i >= 8 else -2.0,
            "r_multiple": 1.5 if i >= 8 else None,
            "outcome": "TP" if i >= 8 else "NA",
            "bars": 20,
        })
    stats = scorecard.summarize(rows)
    qs = stats["quintiles"]
    assert len(qs) == 2
    q1 = next(x for x in qs if x["quintile"] == 1)
    q5 = next(x for x in qs if x["quintile"] == 5)
    assert q5["avg_fwd"] > q1["avg_fwd"]
    assert q5["ci95"] is not None
    txt = scorecard.format_summary(stats)
    assert "Breakdown Kuintil Live" in txt


def test_evaluate_powerscore_and_cek_offline():
    rng = np.random.default_rng(11)
    frames = {}
    for idx, sym in enumerate(["AA", "BB", "CC", "DD", "EE", "FF"]):
        rets = rng.normal(0.0005 * (idx - 1), 0.015, size=140)
        closes = (35.0 if idx == 0 else 1200.0) * np.cumprod(1.0 + rets)
        vol = 500_000 if idx == 0 else 8_000_000
        frames[sym] = make_prices(closes, start="2025-01-01", volume=vol)

    res = evaluate_powerscore.evaluate_powerscore_and_cek(frames, horizon=20, stride=10)
    assert res["ok"] is True
    assert res["n_rows"] > 0
    assert set(res["ic_comparison"].keys()) == {
        "tech_9sig_momentum", "relatif_12f_validated", "hybrid_rel_plus_9sig",
    }
    assert "vetoed" in res["veto_audit"] and "non_vetoed" in res["veto_audit"]
    assert res["veto_audit"]["vetoed"]["n"] > 0

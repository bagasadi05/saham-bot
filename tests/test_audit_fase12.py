"""Tes regresi Fase 1 & 2 PLAN-PERBAIKAN-AUDIT.md (F-01 s.d. F-09)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

import config
from core import rencana, risk, saran, store


def _rets(seed: int, sigma: float, n: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.Series(rng.normal(0.0, sigma, n), index=idx)


def test_f01_f09_saran_position_size_capped_dan_konsisten_dengan_rencana():
    res_mock = {
        "ticker": "BBCA",
        "snap": {"close": 9000.0, "ret20d": 2.0, "ret1d": 0.5},
        "score": {"pct": 40.0, "rating": "BUY"},
        "sr": {"support": 8500.0, "resistance": 9500.0},
        "ind": pd.DataFrame({"atr14": [135.0], "close": [9000.0]}),
    }
    cand = saran.build_candidate(res_mock, None, None, modal=10_000_000)
    assert cand["value"] <= 10_000_000 * config.DEFAULT_MAX_POS_PCT + 1e-6
    assert cand["lots"] == 2
    assert saran.MAX_POS_PCT == risk.MAX_POS_PCT == rencana.MAX_POS_PCT == config.DEFAULT_MAX_POS_PCT


def test_f02_var_tidak_terkecilkan_oleh_posisi_riwayat_pendek():
    r1 = _rets(1, 0.015, 160)
    r2 = _rets(2, 0.015, 160)
    short_r = _rets(3, 0.015, 10)

    pos2 = [
        {"ticker": "AAA", "lots": 10, "avg_price": 1000.0},
        {"ticker": "BBB", "lots": 10, "avg_price": 1000.0},
    ]
    q2 = {"AAA": {"close": 1000.0, "rets": r1}, "BBB": {"close": 1000.0, "rets": r2}}
    out2 = risk.portfolio_risk(pos2, q2)
    assert out2["var95_pct"] is not None and out2["hist_var95_pct"] is not None
    assert out2["var_coverage_pct"] == 100.0

    pos3 = pos2 + [{"ticker": "CCC", "lots": 10, "avg_price": 1000.0}]
    q3 = {**q2, "CCC": {"close": 1000.0, "rets": short_r}}
    out3 = risk.portfolio_risk(pos3, q3)
    assert out3["var_coverage_pct"] < 80.0
    assert out3["var95_pct"] is None
    assert out3["hist_var95_pct"] is None
    assert "cakupan" in (out3["var_note"] or "").lower()

    pos_small = pos2 + [{"ticker": "CCC", "lots": 2, "avg_price": 1000.0}]
    out_small = risk.portfolio_risk(pos_small, q3)
    assert out_small["var_coverage_pct"] >= 80.0
    assert out_small["var95_pct"] == out2["var95_pct"]
    assert out_small["hist_var95_pct"] == out2["hist_var95_pct"]


def test_f03_f04_var_min_days_dan_kupiec_oos():
    r80 = _rets(10, 0.02, 80)
    out80 = risk.portfolio_risk(
        [{"ticker": "AAA", "lots": 10, "avg_price": 1000.0}],
        {"AAA": {"close": 1000.0, "rets": r80}},
    )
    assert out80["hist_var95_pct"] is None
    assert out80["var_breaches"] is None

    r260 = _rets(11, 0.02, 260)
    out260 = risk.portfolio_risk(
        [{"ticker": "AAA", "lots": 10, "avg_price": 1000.0}],
        {"AAA": {"close": 1000.0, "rets": r260}},
    )
    assert out260["hist_var95_pct"] is not None
    assert out260["hist_es95_pct"] is not None
    assert out260["var_kupiec_p"] is not None and 0.0 <= out260["var_kupiec_p"] <= 1.0


def test_f06_f07_update_paper_gap_sl_dan_multibar(tmp_store):
    store.set_paper_record(100, "GAP1", {
        "status": "open", "entry": 1000.0, "sl": 950.0, "tp": 1100.0,
        "filled_at": "2026-01-05", "lots": 10, "value": 1_000_000.0,
    })
    msgs = saran.update_paper(
        100,
        {"GAP1": {"open": 900.0, "high": 920.0, "low": 890.0, "close": 910.0, "date": "2026-01-06"}},
        today=date(2026, 1, 6),
    )
    rec = store.get_paper(100)["GAP1"]
    assert msgs and "GAP" in msgs[0]
    assert rec["hit"] == "sl_gap"
    assert rec["exit_price"] == 900.0

    store.set_paper_record(100, "MULTI", {
        "status": "open", "entry": 1000.0, "sl": 950.0, "tp": 1100.0,
        "filled_at": "2026-01-05", "lots": 10, "value": 1_000_000.0,
    })
    df_multi = pd.DataFrame(
        {
            "open": [1000.0, 990.0, 1020.0],
            "high": [1010.0, 995.0, 1080.0],
            "low": [980.0, 940.0, 1010.0],
            "close": [995.0, 960.0, 1070.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )
    msgs_m = saran.update_paper(100, {"MULTI": df_multi}, today=date(2026, 1, 7))
    rec_m = store.get_paper(100)["MULTI"]
    assert msgs_m and rec_m["status"] == "closed"
    assert rec_m["hit"] == "sl"
    assert rec_m["closed_at"] == "2026-01-06"


def test_f08_fill_pending_tidak_fallback_ke_bar_terakhir(tmp_store):
    store.set_paper_record(100, "NOFB", {
        "status": "pending", "queued": "2026-01-05", "atr": 30.0,
    })
    df_bad = pd.DataFrame(
        {"open": [1000.0, 1050.0, 1060.0]},
        index=pd.to_datetime(["2026-01-06", "2026-01-06", "2026-01-07"]),
    )
    msgs = saran.fill_pending(100, {"NOFB": df_bad}, modal=10_000_000)
    assert msgs == []
    assert store.get_paper(100)["NOFB"]["status"] == "pending"

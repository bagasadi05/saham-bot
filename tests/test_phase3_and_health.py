"""Tes offline Fase 3, 4 & 6: deteksi gorengan, days_to_exit, stress_test, health monitor & ablasi."""
from __future__ import annotations

import datetime as dt
import numpy as np

from core import analysis, cek, health, risk, risk_flags
from research import phase3_ablation
from tests.conftest import make_prices


def test_detect_speculative_risk_gorengan_and_cek_veto():
    # Saham normal -> NORMAL, tidak kena veto
    df_ok = make_prices(np.linspace(1000, 1080, 60), volume=5_000_000)
    ind_ok = analysis.compute_indicators(df_ok)
    rf_ok = risk_flags.detect_speculative_risk(ind_ok, {"per": 12.0, "roe": 16.0})
    assert rf_ok["available"] is True
    assert rf_ok["speculative_risk"] is False
    assert rf_ok["level"] == "NORMAL"

    # Saham gorengan: rentetan ARA 3x + lonjakan volume > 3 sigma + reli >35% + PER negatif
    base = np.full(45, 200.0)
    pump = np.array([200.0, 250.0, 312.0, 390.0, 485.0])  # +25% beruntun
    closes = np.concatenate([base, pump])
    df_pump = make_prices(closes, volume=1_000_000)
    df_pump.iloc[-1, df_pump.columns.get_loc("volume")] = 50_000_000.0
    ind_pump = analysis.compute_indicators(df_pump)

    rf_bad = risk_flags.detect_speculative_risk(ind_pump, {"per": -15.0, "roe": -10.0})
    assert rf_bad["speculative_risk"] is True
    assert rf_bad["level"] == "RISIKO_SPEKULATIF"
    assert rf_bad["veto_reason"] is not None

    # Masuk sebagai veto di cek.build()
    res_p = {
        "ticker": "GRGN",
        "ind": ind_pump,
        "score": analysis.score_stock(ind_pump),
        "snap": analysis.last_snapshot(ind_pump),
        "weekly": {"direction": "naik"},
    }
    chk = cek.build(
        res=res_p, fund=None, fund_raw={"per": -15.0, "roe": -10.0},
        nsent=None, bandar_row=None, agenda=None, agenda_days=None,
        peers=None, value_avg20=10_000_000_000.0,
    )
    assert chk["verdict"] == "HINDARI"
    assert any("RISIKO_SPEKULATIF" in v for v in chk["vetoes"])


def test_risk_days_to_exit_and_stress_tests():
    assert risk.days_to_exit(100_000_000, 1_000_000_000, max_part=0.20) == 0.5
    assert risk.days_to_exit(100_000_000, 0) is None

    st = risk.stress_test_portfolio([
        {"ticker": "BBCA", "value_rp": 60_000_000},
        {"ticker": "BBRI", "value_rp": 40_000_000},
    ])
    assert len(st) == 3
    assert st[0]["est_loss_rp"] == -10_000_000
    assert st[1]["est_loss_rp"] == -20_000_000
    assert st[2]["est_loss_rp"] == -35_000_000


def test_data_health_monitor_fresh_vs_stale():
    fresh = health.check_data_health(
        meta={"trade_date": "2026-11-27", "scanned_at": "2026-11-27T16:15:00", "ok": 900, "failed": 44},
        today=dt.date(2026, 11, 28),
    )
    assert fresh["status"] == "HEALTHY"
    assert fresh["is_stale"] is False

    stale = health.check_data_health(
        meta={"trade_date": "2026-11-01", "scanned_at": "2026-11-01T16:15:00", "ok": 900, "failed": 44},
        today=dt.date(2026, 11, 28),
    )
    assert stale["status"] == "STALE"
    assert stale["is_stale"] is True
    assert len(stale["warnings"]) >= 1

    empty = health.check_data_health(meta={}, today=dt.date(2026, 11, 28))
    assert empty["is_stale"] is True


def test_phase3_ablation_offline():
    rng = np.random.default_rng(19)
    frames = {}
    for idx, sym in enumerate(["A1", "B2", "C3", "D4", "E5", "F6", "G7", "H8"]):
        rets = rng.normal(0.0004 + idx * 0.0003, 0.014, size=180)
        closes = 1200.0 * np.cumprod(1.0 + rets)
        frames[sym] = make_prices(closes, start="2024-06-01", volume=6_000_000)

    out = phase3_ablation.run_ablation(frames, horizon=20, step=20)
    assert out["ok"] is True
    assert "baseline_12f" in out["ablation"]
    assert "plus_mom_12_1_25pct" in out["ablation"]
    assert "combo_12f_mom_liq" in out["ablation"]

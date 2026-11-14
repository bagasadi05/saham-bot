"""C3: gate ARA/ARB memakai aturan 19 Okt–31 Des 2026 dan tick BEI."""
from __future__ import annotations

import datetime as dt

import pandas as pd

import backtest_real
from core import pricing

IN_WINDOW = dt.date(2026, 11, 2)


def test_auto_reject_only_inside_known_window():
    assert pricing.auto_reject_pct(1000, dt.date(2026, 10, 18)) is None
    assert pricing.auto_reject_pct(1000, IN_WINDOW) == 25.0
    assert pricing.auto_reject_pct(1000, dt.date(2026, 12, 31)) == 25.0
    assert pricing.auto_reject_pct(1000, dt.date(2027, 1, 4)) is None


def test_auto_reject_price_bands():
    assert pricing.auto_reject_pct(150, IN_WINDOW) == 35.0
    assert pricing.auto_reject_pct(5000, IN_WINDOW) == 25.0
    assert pricing.auto_reject_pct(5001, IN_WINDOW) == 20.0
    assert pricing.auto_reject_pct(5, IN_WINDOW) is None   # batas absolut Rp1


def test_ara_lock_detection():
    assert pricing.is_ara_locked(1200, 1000, IN_WINDOW) is False   # +20%
    assert pricing.is_ara_locked(1260, 1000, IN_WINDOW) is True    # +26%
    assert pricing.is_ara_locked(1300, 1000, dt.date(2026, 1, 5)) is False


def test_ara_locked_series_flags_only_in_window():
    idx = pd.bdate_range("2026-10-19", periods=5)
    closes = [1000, 1000, 1300, 1300, 1300]          # lompatan +30%
    frame = pd.DataFrame({"open": closes, "high": closes, "low": closes,
                          "close": closes, "volume": 1e6}, index=idx)
    flags = backtest_real.ara_locked_series(frame)
    assert flags.sum() >= 1

    old = pd.DataFrame({"open": closes, "high": closes, "low": closes,
                        "close": closes, "volume": 1e6},
                       index=pd.bdate_range("2025-01-06", periods=5))
    assert backtest_real.ara_locked_series(old).sum() == 0


def test_atr_levels_are_valid_ticks():
    sl, tp = backtest_real.atr_levels(9000.0, 300.0)
    assert (sl, tp) == (8700.0, 9600.0)
    assert sl % 25 == 0 and tp % 25 == 0
    assert sl < 9000 < tp


def test_atr_levels_never_cross_entry():
    sl, tp = backtest_real.atr_levels(100.0, 500.0)
    assert 0 < sl < 100 < tp


def test_auto_reject_status_reports_expiration_explicitly(monkeypatch):
    st_in = pricing.auto_reject_status(_dt_in := IN_WINDOW)
    assert st_in["active"] is True
    assert st_in["expired"] is False
    assert st_in["asym_arb"] is True

    st_exp = pricing.auto_reject_status(dt.date(2027, 1, 5))
    assert st_exp["active"] is False
    assert st_exp["expired"] is True
    assert "IDX_ARA_WINDOW_END" in st_exp["note"]

    monkeypatch.setenv("IDX_ARA_WINDOW_END", "2027-06-30")
    assert pricing.auto_reject_pct(1000, dt.date(2027, 2, 1)) == 25.0
    assert pricing.arb_floor_price(1000, dt.date(2027, 2, 1)) == 930.0
    assert _dt_in == IN_WINDOW


def test_arb_floor_price_kep_00158_dan_kep_00136():
    # Jendela ARB asimetris 7% (Kep-00158/BEI/10-2026)
    assert pricing.arb_floor_price(1000, IN_WINDOW) == 930.0
    assert pricing.arb_floor_price(1025, IN_WINDOW) == 955.0  # ceil tick 5 agar <=7%
    assert pricing.arb_floor_price(5000, IN_WINDOW) == 4650.0
    assert pricing.arb_floor_price(14, IN_WINDOW) == 13.0     # Rp11-14 langkah Rp1
    assert pricing.arb_floor_price(10, IN_WINDOW) == 9.0      # Rp1-10 langkah Rp1
    assert pricing.arb_floor_price(1, IN_WINDOW) == 1.0
    # Kep-00136 (mulai 28 Sep 2026): floor reguler Rp1, bukan Rp50
    assert pricing.arb_floor_price(50, IN_WINDOW) == 47.0
    assert pricing.arb_floor_price(52, dt.date(2026, 9, 1)) == 50.0
    # Papan Pemantauan Khusus: 10% limit
    assert pricing.arb_floor_price(1000, IN_WINDOW, board="Pemantauan Khusus") == 900.0
    # Di luar jendela asimetris -> kembali ke aturan simetris umum
    assert pricing.arb_floor_price(1000, dt.date(2027, 2, 1)) == 750.0


def test_check_arb_gap_risk_flags_below_floor_and_deep_sl():
    ok = pricing.check_arb_gap_risk(1000, 950, dt=IN_WINDOW)
    assert ok["risk"] is False
    assert ok["warning"] == ""

    bad = pricing.check_arb_gap_risk(1000, 920, dt=IN_WINDOW)
    assert bad["risk"] is True
    assert bad["below_floor"] is True
    assert bad["deeper_than_7pct"] is True
    assert bad["floor"] == 930.0
    assert "ARB" in bad["warning"]

    gap_prev = pricing.check_arb_gap_risk(960, 920, prev_close=1000, dt=IN_WINDOW)
    assert gap_prev["risk"] is True
    assert gap_prev["below_floor"] is True

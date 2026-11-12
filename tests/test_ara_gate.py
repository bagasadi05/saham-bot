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

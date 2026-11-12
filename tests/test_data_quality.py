"""Pemeriksaan kualitas OHLCV: flag yang terlihat, bukan bar yang hilang diam-diam."""
from __future__ import annotations

import pandas as pd

from core import data_quality


def _frame(closes, index=None, volume=1_000.0):
    idx = index if index is not None else pd.bdate_range("2026-08-03", periods=len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": volume,
    }, index=idx)


def test_clean_frame_has_no_flags():
    df = _frame([100 + i for i in range(60)])
    q = data_quality.check(df, today=df.index[-1].date())
    assert q["ok"] is True
    assert q["flags"] == {}
    assert data_quality.summary_line(q) == ""


def test_short_history_is_flagged():
    df = _frame([100] * 10)
    q = data_quality.check(df, today=df.index[-1].date())
    assert q["flags"]["short_history"] == 10
    assert "riwayat" in data_quality.summary_line(q)


def test_volume_zero_and_large_jump_are_flagged():
    closes = [100.0] * 20 + [200.0] + [200.0] * 9
    df = _frame(closes)
    df.loc[df.index[10], "volume"] = 0

    q = data_quality.check(df, today=df.index[-1].date())

    assert q["flags"]["volume_nol"] == 1
    assert q["flags"]["unexplained_jump"] >= 1


def test_session_gap_is_flagged():
    idx = pd.DatetimeIndex(
        list(pd.bdate_range("2026-01-05", periods=20))
        + list(pd.bdate_range("2026-02-16", periods=20))
    )
    df = _frame([100.0] * 40, index=idx)
    q = data_quality.check(df, today=idx[-1].date())
    assert q["flags"]["session_gap"] >= 1


def test_stale_data_uses_trading_days(monkeypatch):
    df = _frame([100.0] * 40)
    monkeypatch.setattr(data_quality.marketcal, "trading_days_between",
                        lambda start, end: 7)
    q = data_quality.check(df, today=df.index[-1].date())
    assert q["flags"]["stale_days"] == 7
    assert "7 hari bursa lalu" in data_quality.summary_line(q)


def test_empty_frame_is_reported():
    q = data_quality.check(pd.DataFrame(), today=None)
    assert q["ok"] is False
    assert q["flags"]["no_data"] == 1
    assert "tanpa data" in data_quality.summary_line(q)

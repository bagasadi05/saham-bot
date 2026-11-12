"""Tanggal bursa harus mengikuti WIB, bukan zona waktu server (UTC)."""
from __future__ import annotations

import datetime as dt

from core import marketcal


def test_last_trading_day_uses_wib_date(monkeypatch):
    # 12 Nov 2026 23:30 UTC == 13 Nov 2026 06:30 WIB (Jumat)
    fake_now = dt.datetime(2026, 11, 13, 6, 30, tzinfo=marketcal.WIB)
    monkeypatch.setattr(marketcal, "now_wib", lambda: fake_now)
    monkeypatch.setattr(marketcal, "is_trading_day", lambda d: d.weekday() < 5)

    assert marketcal.last_trading_day() == dt.date(2026, 11, 13)


def test_last_trading_day_still_accepts_explicit_date(monkeypatch):
    monkeypatch.setattr(marketcal, "is_trading_day", lambda d: d.weekday() < 5)
    assert marketcal.last_trading_day(dt.date(2026, 11, 7)) == dt.date(2026, 11, 6)

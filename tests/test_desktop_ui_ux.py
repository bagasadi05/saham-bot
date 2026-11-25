"""Tes offline untuk helper tampilan murni UI/UX aplikasi desktop (`desktop.py`).

Semua tes berjalan tanpa display (tidak membuat Tk) dan tanpa jaringan.
"""
from __future__ import annotations

import datetime as dt

import desktop
from core import marketcal


# ---------------------------------------------------------------------------
# desktop.py: helper tampilan murni (akan diisi setelah helper diimplementasi)
# ---------------------------------------------------------------------------

def test_desktop_pure_helpers():
    assert desktop.spinner_frame(0) == desktop.SPINNER_FRAMES[0]
    assert desktop.spinner_frame(3 + len(desktop.SPINNER_FRAMES)) == desktop.SPINNER_FRAMES[3]
    assert desktop.countdown_text(0) == "00:00"
    assert desktop.countdown_text(65) == "01:05"
    assert desktop.countdown_text(3661) == "1:01:01"
    assert desktop.countdown_text(-5) == "00:00"
    assert desktop.tab_label("⭐ Watchlist", 5) == "⭐ Watchlist (5)"
    assert desktop.tab_label("⭐ Watchlist", None) == "⭐ Watchlist"


def test_market_clock_text_open_closed_and_weekend():
    day = marketcal.last_trading_day(dt.date(2026, 11, 25))
    open_dt = dt.datetime(day.year, day.month, day.day, 10, 0, 5, tzinfo=marketcal.WIB)
    text, status = desktop.market_clock_text(open_dt)
    assert "10:00:05" in text and status == "open"

    closed_dt = dt.datetime(day.year, day.month, day.day, 20, 30, tzinfo=marketcal.WIB)
    text2, status2 = desktop.market_clock_text(closed_dt)
    assert "20:30:00" in text2 and status2 == "closed"

    # Minggu selalu tutup walau jam sesi
    sunday = dt.date(2026, 11, 29)
    assert sunday.weekday() == 6
    _, status3 = desktop.market_clock_text(dt.datetime(2026, 11, 29, 10, 0, tzinfo=marketcal.WIB))
    assert status3 == "closed"

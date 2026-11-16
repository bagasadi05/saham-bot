"""T1/T3: pengingat dividen tahan blip jaringan dan mencakup seluruh watchlist."""
from __future__ import annotations

import asyncio
import datetime as dt
import types

import pytest
from telegram.error import NetworkError

import bot
from core import store


class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.fail_next = 0

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise NetworkError("blip jaringan")
        self.sent.append((chat_id, text))


def _app():
    return types.SimpleNamespace(bot=_Bot())


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def _noop(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _noop)


def test_reminder_retried_after_network_failure(tmp_store, monkeypatch):
    today = dt.date(2026, 11, 16)
    store.set_auto(10, True)
    store.watchlist_add(10, "BBCA")
    monkeypatch.setattr(
        bot.marketcal, "upcoming_dividend_exdate",
        lambda t, today=None: {"ex_date": today + dt.timedelta(days=3),
                               "amount": 100.0})

    app = _app()
    app.bot.fail_next = 5          # habiskan 3 percobaan di putaran 1 + 2 di putaran 2

    assert asyncio.run(bot.check_dividend_reminders(app, today=today)) == 0
    assert app.bot.sent == []

    # Putaran berikutnya harus mencoba lagi (dulu: hilang permanen karena
    # tanda dedup sudah ditulis sebelum kirim).
    assert asyncio.run(bot.check_dividend_reminders(app, today=today)) == 1
    assert len(app.bot.sent) == 1

    # Dan tidak diulang lagi setelah sukses.
    assert asyncio.run(bot.check_dividend_reminders(app, today=today)) == 0
    assert len(app.bot.sent) == 1


def test_reminder_covers_full_watchlist(tmp_store, monkeypatch):
    today = dt.date(2026, 11, 16)
    store.set_auto(10, True)
    symbols = [f"T{i:02d}" for i in range(20)]
    for sym in symbols:
        store.watchlist_add(10, sym)

    monkeypatch.setattr(
        bot.marketcal, "upcoming_dividend_exdate",
        lambda t, today=None: {"ex_date": today + dt.timedelta(days=3),
                               "amount": None})

    app = _app()
    sent = asyncio.run(bot.check_dividend_reminders(app, today=today))

    assert sent == 20
    assert len(app.bot.sent) == 20

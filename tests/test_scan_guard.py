"""Tes penjaga scan tunggal.

Bug yang dijaga: job harian 16:15 memanggil `screener.scan` tanpa memeriksa
penjaga yang dipakai `/scan`, sehingga dua pemindaian ±944 emiten bisa
berjalan bersamaan (kuota Yahoo ganda, dua penulisan snapshot).
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import bot
from core import screener


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class _FakeApp:
    def __init__(self):
        self.bot = _FakeBot()


def test_guard_skips_when_other_scan_runs(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(screener, "scan",
                        lambda force: calls.append(force) or {"cached": False})
    monkeypatch.setattr(bot, "_screener_running", True)

    assert asyncio.run(bot._scan_with_guard(False)) is None
    assert calls == []
    # penjaga milik pemanggil lain tidak boleh dilepas oleh yang melewati
    assert bot._screener_running is True


def test_guard_releases_flag_on_success(monkeypatch):
    monkeypatch.setattr(screener, "scan", lambda force: {"cached": False})
    monkeypatch.setattr(bot, "_screener_running", False)

    assert asyncio.run(bot._scan_with_guard(False)) == {"cached": False}
    assert bot._screener_running is False


def test_guard_releases_flag_on_error(monkeypatch):
    def boom(force):
        raise RuntimeError("yahoo mati")

    monkeypatch.setattr(screener, "scan", boom)
    monkeypatch.setattr(bot, "_screener_running", False)

    with pytest.raises(RuntimeError):
        asyncio.run(bot._scan_with_guard(False))
    assert bot._screener_running is False


def test_guard_serializes_concurrent_calls(monkeypatch):
    async def main():
        started = asyncio.Event()
        release = threading.Event()
        calls: list[bool] = []

        def slow_scan(force):
            calls.append(force)
            started.set()
            release.wait(5)
            return {"cached": False}

        monkeypatch.setattr(screener, "scan", slow_scan)
        monkeypatch.setattr(bot, "_screener_running", False)

        first = asyncio.create_task(bot._scan_with_guard(False))
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await bot._scan_with_guard(False) is None  # ditolak, tidak menumpuk
        release.set()
        assert await first == {"cached": False}
        assert calls == [False]

    asyncio.run(main())


def test_daily_job_skips_when_scan_busy(monkeypatch):
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(bot, "_screener_running", True)
    monkeypatch.setattr(screener, "scan",
                        lambda force: pytest.fail("scan tidak boleh dipanggil"))

    app = _FakeApp()
    asyncio.run(bot.full_market_scan_job(app))
    assert app.bot.sent == []
    assert bot._screener_running is True


def test_daily_job_runs_and_releases_flag(monkeypatch):
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(bot, "_screener_running", False)
    monkeypatch.setattr(screener, "scan", lambda force: {"cached": True})

    app = _FakeApp()
    asyncio.run(bot.full_market_scan_job(app))
    assert app.bot.sent == []          # cached → tidak mengirim apa pun
    assert bot._screener_running is False

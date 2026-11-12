"""Tes offline notifikasi admin saat job latar gagal (Fase 4.3)."""
from __future__ import annotations

import asyncio

import bot


class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _App:
    def __init__(self):
        self.bot = _Bot()


def test_admin_chat_id_dari_env(monkeypatch):
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "555")
    assert bot.admin_chat_id() == 555


def test_admin_chat_id_fallback_admin_ids(monkeypatch):
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "")
    monkeypatch.setattr(bot, "ADMIN_IDS", {777, 666})
    assert bot.admin_chat_id() == 666


def test_tanpa_admin_tidak_mengirim(monkeypatch):
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "")
    monkeypatch.setattr(bot, "ADMIN_IDS", set())
    app = _App()
    assert asyncio.run(bot.notify_admin(app, "uji")) is False
    assert app.bot.sent == []


def test_notify_admin_mengirim(monkeypatch):
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "555")
    app = _App()
    assert asyncio.run(bot.notify_admin(app, "halo admin")) is True
    assert app.bot.sent[0][0] == 555


def test_job_scan_gagal_memberi_tahu_admin(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("Yahoo menolak")

    monkeypatch.setattr(bot, "_scan_with_guard", boom)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "555")
    app = _App()
    asyncio.run(bot.full_market_scan_job(app))
    assert app.bot.sent
    assert "Screener harian gagal" in app.bot.sent[0][1]
    assert "Yahoo menolak" in app.bot.sent[0][1]


def test_job_scan_banyak_gagal_memberi_tahu(monkeypatch):
    async def fake_scan(*a, **k):
        return {"cached": False, "meta": {"failed": 250, "universe": 900,
                                          "ok": 650}, "counts": {}}

    monkeypatch.setattr(bot, "_scan_with_guard", fake_scan)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "555")
    monkeypatch.setattr(bot.store, "get_auto_chats", lambda: [])
    app = _App()
    asyncio.run(bot.full_market_scan_job(app))
    assert app.bot.sent
    assert "banyak kegagalan" in app.bot.sent[0][1]

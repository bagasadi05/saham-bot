"""Wiring main(): semua perintah, job, error handler terdaftar tanpa polling nyata."""
from __future__ import annotations

import bot


class _FakeJobQueue:
    def __init__(self):
        self.registered: list[tuple] = []

    def run_daily(self, cb, time=None, name=None, **kw):
        self.registered.append(("daily", name, cb))

    def run_repeating(self, cb, interval=None, first=None, name=None, **kw):
        self.registered.append(("repeating", name, cb))


class _FakeApp:
    def __init__(self):
        self.handlers: list = []
        self.error_handlers: list = []
        self.polled = False
        self.job_queue = _FakeJobQueue()

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)

    def run_polling(self, **kwargs):
        self.polled = True


class _Builder:
    def __init__(self, app):
        self._app = app

    def token(self, *_a, **_k):
        return self

    def concurrent_updates(self, *_a, **_k):
        return self

    def connection_pool_size(self, *_a, **_k):
        return self

    def rate_limiter(self, *_a, **_k):
        return self

    def post_init(self, *_a, **_k):
        return self

    def build(self):
        return self._app


def test_main_wiring_lengkap(monkeypatch):
    app = _FakeApp()

    class _FakeApplication:
        @staticmethod
        def builder():
            return _Builder(app)

    monkeypatch.setattr(bot, "Application", _FakeApplication)
    monkeypatch.setattr(bot.config, "BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(bot.config, "BACKUP_AUTO", True)
    monkeypatch.setattr(bot.config, "BACKUP_DIR", "backups")
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("REQUIRE_ALLOWLIST", "0")

    bot.main()

    assert app.polled is True
    assert app.error_handlers == [bot.on_error]

    cmds: set[str] = set()
    for handler in app.handlers:
        cmds.update(getattr(handler, "commands", None) or ())

    expected = {
        "start", "menu", "mode", "help", "analisis", "skor", "berita",
        "fundamental", "backtest", "watch", "saran", "sinyal", "rencana",
        "paket", "cek", "portofolio", "jurnal", "pasar", "market", "sektor",
        "scan", "beli", "jual", "bandar", "banding", "alert", "agenda",
        "validasi", "jejak", "modal", "auto", "status", "id", "belajar",
    }
    assert expected <= cmds
    # 32 CommandHandler + CallbackQueryHandler + MessageHandler
    assert len(app.handlers) == 34

    names = {name for _, name, _ in app.job_queue.registered}
    assert {"morning-scan", "fill-open", "full-market-scan",
            "closing-market-report", "weekly-journal", "heartbeat",
            "backup-store"} <= names

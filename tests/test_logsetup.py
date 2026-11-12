"""Tes offline logging JSON (LOG_JSON=1) tanpa mengganggu handler pytest."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

import bot
from core import logsetup


@pytest.fixture
def root_bersih():
    """Simpan handler root; pulihkan setelah tes (setup_logging membuangnya)."""
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    old_disabled = logging.root.manager.disable
    try:
        yield root
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
        logging.root.manager.disable = old_disabled


def test_formatter_json_field_dasar():
    rec = logging.LogRecord("uji", logging.INFO, __file__, 1, "halo %s",
                            ("dunia",), None)
    out = json.loads(logsetup.JsonFormatter().format(rec))
    assert out["level"] == "INFO"
    assert out["logger"] == "uji"
    assert out["msg"] == "halo dunia"
    assert out["ts"].endswith("+00:00")


def test_formatter_json_membawa_field_terstruktur():
    rec = logging.LogRecord("uji", logging.INFO, __file__, 1, "command", None,
                            None)
    rec.cmd, rec.chat_id, rec.duration_ms, rec.ok = "cmd_cek", 100, 12.5, True
    out = json.loads(logsetup.JsonFormatter().format(rec))
    assert out["cmd"] == "cmd_cek"
    assert out["chat_id"] == 100
    assert out["duration_ms"] == 12.5
    assert out["ok"] is True


def test_formatter_json_menyertakan_traceback():
    try:
        raise ValueError("rusak")
    except ValueError:
        import sys
        exc = sys.exc_info()
    rec = logging.LogRecord("uji", logging.ERROR, __file__, 1, "gagal", None,
                            exc)
    out = json.loads(logsetup.JsonFormatter().format(rec))
    assert "ValueError: rusak" in out["exc"]


def test_setup_logging_json_saat_env(monkeypatch, root_bersih):
    monkeypatch.setenv("LOG_JSON", "1")
    assert logsetup.setup_logging() is True
    assert isinstance(root_bersih.handlers[0].formatter, logsetup.JsonFormatter)


def test_setup_logging_teks_tanpa_env(monkeypatch, root_bersih):
    monkeypatch.delenv("LOG_JSON", raising=False)
    assert logsetup.setup_logging() is False
    assert not isinstance(root_bersih.handlers[0].formatter,
                          logsetup.JsonFormatter)


def test_timed_menulis_baris_terstruktur(monkeypatch, caplog):
    monkeypatch.setattr(bot.logsetup, "json_enabled", lambda: True)

    class _Chat:
        id = 42

    class _Update:
        effective_chat = _Chat()

    @bot.timed
    async def contoh(update):
        return "ok"

    with caplog.at_level(logging.INFO, logger="sahambuddy"):
        assert asyncio.run(contoh(_Update())) == "ok"
    rec = next(r for r in caplog.records if r.getMessage() == "command")
    assert rec.cmd == "contoh"
    assert rec.chat_id == 42
    assert rec.ok is True
    assert rec.duration_ms >= 0


def test_timed_menandai_gagal(monkeypatch, caplog):
    monkeypatch.setattr(bot.logsetup, "json_enabled", lambda: True)

    @bot.timed
    async def rusak(update):
        raise RuntimeError("x")

    with caplog.at_level(logging.INFO, logger="sahambuddy"):
        with pytest.raises(RuntimeError):
            asyncio.run(rusak(None))
    rec = next(r for r in caplog.records if r.getMessage() == "command")
    assert rec.ok is False

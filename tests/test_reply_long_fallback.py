"""T4: fallback teks polos harus meng-unescape entity dan tidak menelan error jaringan."""
from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest, NetworkError

import bot


class _HtmlRejectingMessage:
    def __init__(self):
        self.plain: list[str] = []

    async def reply_text(self, text, **kwargs):
        if kwargs.get("parse_mode") is not None:
            raise BadRequest("Can't parse entities")
        self.plain.append(text)


class _OfflineMessage:
    async def reply_text(self, text, **kwargs):
        raise NetworkError("jaringan mati")


def test_fallback_unescapes_html_entities(monkeypatch):
    async def _noop(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _noop)
    msg = _HtmlRejectingMessage()

    asyncio.run(bot.reply_long(msg, "Halo <b>BBCA &amp; TLKM</b> — &lt;naik&gt;"))

    assert msg.plain == ["Halo BBCA & TLKM — <naik>"]
    assert "&amp;" not in msg.plain[0]


def test_network_error_is_not_treated_as_html_fallback(monkeypatch):
    async def _noop(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _noop)

    with pytest.raises(NetworkError):
        asyncio.run(bot.reply_long(_OfflineMessage(), "tes"))

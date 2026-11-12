"""Regresi: default alert harus mencakup SEMUA jenis di core/alerts.py."""
from __future__ import annotations

import asyncio

import bot
from core import alerts, store


class _Msg:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _Upd:
    def __init__(self):
        self.effective_message = _Msg()
        self.effective_chat = type("C", (), {"id": 42})()
        self.effective_user = type("U", (), {"id": 42})()


def test_alert_add_default_covers_all_kinds(tmp_store):
    upd = _Upd()
    asyncio.run(bot._do_alert_add(upd, "BBCA", from_menu=False))

    saved = set(store.get_alerts(42)["BBCA"])
    assert saved == set(alerts.ALL_KINDS)
    assert {"breakdown", "vol_spike"} <= saved
    assert upd.effective_message.replies


def test_manual_prompt_lists_all_kinds():
    prompt = bot._MANUAL_PROMPT["alert_add"]
    for kind in alerts.ALL_KINDS:
        assert kind in prompt

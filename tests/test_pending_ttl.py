"""Pilihan menu yang ditinggal user tidak boleh menumpuk selamanya."""
from __future__ import annotations

import time

import bot


def test_pending_roundtrip():
    bot.PENDING.clear()
    bot._pending_ts.clear()
    bot._pending_set(7, "analisis")
    assert bot._pending_get(7) == "analisis"
    bot._pending_clear(7)
    assert bot._pending_get(7) is None
    assert 7 not in bot.PENDING


def test_pending_expires_after_ttl():
    bot.PENDING.clear()
    bot._pending_ts.clear()
    bot._pending_set(8, "skor")
    bot._pending_ts[8] = time.monotonic() - bot.PENDING_TTL - 1

    assert bot._pending_get(8) is None
    assert 8 not in bot.PENDING


def test_pending_prune_removes_only_stale():
    bot.PENDING.clear()
    bot._pending_ts.clear()
    bot._pending_set(9, "berita")
    bot._pending_set(10, "berita")
    bot._pending_ts[9] = time.monotonic() - bot.PENDING_TTL - 1

    bot._pending_prune()

    assert 9 not in bot.PENDING
    assert bot._pending_get(10) == "berita"
    bot.PENDING.clear()
    bot._pending_ts.clear()

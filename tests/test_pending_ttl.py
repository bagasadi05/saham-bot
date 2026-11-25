"""Pilihan menu yang ditinggal user tidak boleh menumpuk selamanya."""
from __future__ import annotations

import asyncio
import time

import bot
from core import guards


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


def test_pending_set_dengan_user_id_menyimpan_owner():
    """CWE-862: pending action harus punya owner agar tidak bisa di-consum user lain."""
    bot.PENDING.clear()
    bot._pending_ts.clear()
    bot._pending_set(42, "exec_buy:BBCA", user_id=12345)
    assert bot._pending_owner(42) == 12345
    bot._pending_clear(42)
    assert bot._pending_owner(42) is None


def test_pending_set_tanpa_user_id_menghapus_owner():
    bot.PENDING.clear()
    bot._pending_ts.clear()
    bot._pending_set(43, "exec_buy:BBCA", user_id=12345)
    assert bot._pending_owner(43) == 12345
    bot._pending_set(43, "modal")  # tanpa user_id → owner dihapus
    assert bot._pending_owner(43) is None
    bot._pending_clear(43)


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeMsg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, uid):
        self.effective_user = _FakeUser(uid)
        self.effective_message = _FakeMsg()


def test_cooldown_ok_atomic_under_concurrency():
    """CWE-367: check-and-set cooldown harus atomik — burst 10 panggilan konkuren
    dari user yang sama hanya boleh lolos 1 kali."""
    guards._last_used.clear()
    name = "scan"  # cooldown 30 detik
    updates = [_FakeUpdate(999) for _ in range(10)]

    async def run():
        results = await asyncio.gather(
            *[guards.cooldown_ok(u, name) for u in updates]
        )
        return results

    results = asyncio.run(run())
    assert sum(1 for r in results if r is True) == 1
    assert sum(1 for r in results if r is False) == 9
    guards._last_used.clear()


def test_cooldown_ok_beda_user_independen():
    guards._last_used.clear()
    name = "analisis"
    u1, u2 = _FakeUpdate(1001), _FakeUpdate(1002)

    async def run():
        r1 = await guards.cooldown_ok(u1, name)
        r2 = await guards.cooldown_ok(u2, name)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1 is True and r2 is True  # user berbeda → keduanya lolos
    guards._last_used.clear()

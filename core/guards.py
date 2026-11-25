"""Penjaga permintaan: cooldown per pengguna & state "menunggu input" (PENDING).

Dipisah dari `bot.py` karena tidak bergantung pada Telegram selain lewat objek
`update` (duck-typed), dan dipakai banyak handler. Nama-nama lama tetap
di-ekspor dari `bot` sebagai alias, jadi tes yang memanipulasi `bot.PENDING`
atau `bot._last_used` bekerja pada dict yang sama.
"""
from __future__ import annotations

import asyncio
import os
import time

# Command mahal (pemindaian puluhan emiten, backtest 5 tahun) tidak boleh
# dipicu beruntun oleh satu akun: bot bisa dipakai siapa saja kalau
# ALLOWED_USER_IDS kosong, dan tiap panggilan memakan menit CPU + puluhan
# permintaan jaringan.
_COOLDOWN_SECONDS = {
    "analisis": 10, "banding": 10, "saran": 20,
    "bandar": 20, "backtest": 60,
    "scan": 30, "sinyal": 5, "rencana": 20, "cek": 10,
    "portofolio": 5, "sektor": 20, "pasar": 10,
    "berita": 10, "fundamental": 10, "skor": 5,
    "beli": 10, "jual": 10, "tanya": 5, "watch": 5,
    "agenda": 10, "jejak": 10,
}
GLOBAL_MAX_HEAVY = max(1, int(os.getenv("GLOBAL_MAX_HEAVY", "4") or 4))
_global_heavy_sem: asyncio.Semaphore | None = None


def global_heavy_semaphore() -> asyncio.Semaphore:
    """Semaphore global pembatas pekerjaan berat lintas-pengguna (F-17)."""
    global _global_heavy_sem
    if _global_heavy_sem is None:
        _global_heavy_sem = asyncio.Semaphore(GLOBAL_MAX_HEAVY)
    return _global_heavy_sem
_last_used: dict[tuple[int, str], float] = {}
# Lock agar check-and-set cooldown atomik meski ada await di masa depan
# (CWE-367: dengan concurrent_updates(4), dua coroutine bisa interleave
# между check dan write kalau ada await di antaranya).
_cooldown_lock: asyncio.Lock | None = None


def _get_cooldown_lock() -> asyncio.Lock:
    global _cooldown_lock
    if _cooldown_lock is None:
        _cooldown_lock = asyncio.Lock()
    return _cooldown_lock


async def cooldown_ok(update, name: str) -> bool:
    """False (sekaligus balas pesan tunggu) kalau command masih cooldown.

    Check-and-set di dalam lock sehingga atomik: dua update konkuren dari
    user yang sama tidak bisa sama-sama lolos cooldown.
    """
    wait = _COOLDOWN_SECONDS.get(name)
    if not wait:
        return True
    user = getattr(update, "effective_user", None)
    key = (user.id if user else 0, name)
    async with _get_cooldown_lock():
        now = time.monotonic()
        remaining = wait - (now - _last_used.get(key, 0.0))
        if remaining <= 0:
            _last_used[key] = now
            if len(_last_used) > 256:   # pengguna x perintah; buang yang basi
                for k, ts in list(_last_used.items()):
                    if now - ts > 3600:
                        _last_used.pop(k, None)
    if remaining > 0:
        msg = getattr(update, "effective_message", None)
        if msg is not None:
            await msg.reply_text(
                f"⏳ Tenang sebentar — /{name} bisa dipakai lagi dalam "
                f"{int(remaining) + 1} detik.")
        return False
    return True


# chat_id -> aksi yang sedang menunggu input emiten
PENDING: dict[int, str] = {}
PENDING_TTL = 3600.0          # 1 jam: pilihan menu yang ditinggal jangan menumpuk
_pending_ts: dict[int, float] = {}
_pending_owners: dict[int, int] = {}


def _pending_set(chat_id: int, action: str, user_id: int | None = None) -> None:
    _pending_prune()
    PENDING[chat_id] = action
    _pending_ts[chat_id] = time.monotonic()
    if user_id is not None:
        _pending_owners[chat_id] = int(user_id)
    else:
        _pending_owners.pop(chat_id, None)


def _pending_owner(chat_id: int) -> int | None:
    return _pending_owners.get(chat_id)


def _pending_get(chat_id: int) -> str | None:
    action = PENDING.get(chat_id)
    if action is None:
        return None
    ts = _pending_ts.get(chat_id, 0.0)
    if ts and time.monotonic() - ts > PENDING_TTL:
        _pending_clear(chat_id)
        return None
    return action


def _pending_clear(chat_id: int) -> None:
    PENDING.pop(chat_id, None)
    _pending_ts.pop(chat_id, None)
    _pending_owners.pop(chat_id, None)


def _pending_prune() -> None:
    now = time.monotonic()
    for cid, ts in list(_pending_ts.items()):
        if now - ts > PENDING_TTL:
            _pending_clear(cid)

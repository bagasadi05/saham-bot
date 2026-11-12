"""Penjaga permintaan: cooldown per pengguna & state "menunggu input" (PENDING).

Dipisah dari `bot.py` karena tidak bergantung pada Telegram selain lewat objek
`update` (duck-typed), dan dipakai banyak handler. Nama-nama lama tetap
di-ekspor dari `bot` sebagai alias, jadi tes yang memanipulasi `bot.PENDING`
atau `bot._last_used` bekerja pada dict yang sama.
"""
from __future__ import annotations

import time

# Command mahal (pemindaian puluhan emiten, backtest 5 tahun) tidak boleh
# dipicu beruntun oleh satu akun: bot bisa dipakai siapa saja kalau
# ALLOWED_USER_IDS kosong, dan tiap panggilan memakan menit CPU + puluhan
# permintaan jaringan.
_COOLDOWN_SECONDS = {"analisis": 10, "banding": 10, "saran": 20,
                     "bandar": 20, "backtest": 60,
                     "scan": 30, "sinyal": 5, "rencana": 20, "cek": 10,
                     "portofolio": 5, "sektor": 20}
_last_used: dict[tuple[int, str], float] = {}


async def cooldown_ok(update, name: str) -> bool:
    """False (sekaligus balas pesan tunggu) kalau command masih cooldown."""
    wait = _COOLDOWN_SECONDS.get(name)
    if not wait:
        return True
    user = update.effective_user
    key = (user.id if user else 0, name)
    now = time.monotonic()
    remaining = wait - (now - _last_used.get(key, 0.0))
    if remaining > 0:
        await update.effective_message.reply_text(
            f"⏳ Tenang sebentar — /{name} bisa dipakai lagi dalam "
            f"{int(remaining) + 1} detik.")
        return False
    _last_used[key] = now
    if len(_last_used) > 256:   # pengguna x perintah; buang yang sudah basi
        for k, ts in list(_last_used.items()):
            if now - ts > 3600:
                _last_used.pop(k, None)
    return True


# chat_id -> aksi yang sedang menunggu input emiten
PENDING: dict[int, str] = {}
PENDING_TTL = 3600.0          # 1 jam: pilihan menu yang ditinggal jangan menumpuk
_pending_ts: dict[int, float] = {}


def _pending_set(chat_id: int, action: str) -> None:
    _pending_prune()
    PENDING[chat_id] = action
    _pending_ts[chat_id] = time.monotonic()


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


def _pending_prune() -> None:
    now = time.monotonic()
    for cid, ts in list(_pending_ts.items()):
        if now - ts > PENDING_TTL:
            _pending_clear(cid)

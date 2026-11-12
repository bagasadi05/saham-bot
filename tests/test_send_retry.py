"""Tes offline P12: retry pengiriman pesan saat NetworkError/TimedOut."""
from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut

import bot


def test_retry_ulang_lalu_berhasil():
    calls: list[int] = []

    async def call():
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise NetworkError("proxy memutus")
        return "terkirim"

    hasil = asyncio.run(bot.send_with_retry(call, tries=3, base=0))
    assert hasil == "terkirim"
    assert len(calls) == 3


def test_retry_menyerah_setelah_batas_percobaan():
    calls: list[int] = []

    async def call():
        calls.append(1)
        raise TimedOut("lambat")

    with pytest.raises(TimedOut):
        asyncio.run(bot.send_with_retry(call, tries=3, base=0))
    assert len(calls) == 3


def test_error_parser_tidak_diulang():
    """BadRequest (HTML salah) tidak akan berubah bila dikirim ulang."""
    calls: list[int] = []

    async def call():
        calls.append(1)
        raise BadRequest("can't parse entities")

    with pytest.raises(BadRequest):
        asyncio.run(bot.send_with_retry(call, tries=3, base=0))
    assert len(calls) == 1


class _PesanGagal:
    """Pesan palsu: percobaan pertama putus, kedua mencatat teks."""

    def __init__(self):
        self.replies: list[str] = []
        self.gagal = True

    async def reply_text(self, text, **kw):
        if self.gagal:
            self.gagal = False
            raise NetworkError("blip")
        self.replies.append(text)
        return self


def test_reply_long_mengulang_bagian_yang_putus():
    pesan = _PesanGagal()
    asyncio.run(bot.reply_long(pesan, "laporan penting"))
    assert pesan.replies == ["laporan penting"]

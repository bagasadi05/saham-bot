"""Tes D2: Application dibangun dengan konkurensi 4 + rate limiter aktif."""
from __future__ import annotations

from telegram.ext import AIORateLimiter

import bot


def test_build_application_batas_konkurensi_dan_rate_limiter():
    app = bot.build_application("123456:abcdef")
    # `True` berarti 256 update sekaligus — membanjiri Yahoo/SQLite.
    assert app.update_processor.max_concurrent_updates == 4
    assert isinstance(app.bot.rate_limiter, AIORateLimiter)

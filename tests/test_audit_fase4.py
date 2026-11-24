"""Tes regresi Fase 4 PLAN-PERBAIKAN-AUDIT.md (F-16, F-17, F-19, F-20, F-23, F-24)."""
from __future__ import annotations

import datetime as dt

import pytest

import bot
import download_model
from core import data, guards, news, pricing


def test_f16_main_fail_closed_tanpa_allowlist(monkeypatch):
    monkeypatch.setattr(bot.config, "BOT_TOKEN", "123:ABC")
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("ALLOW_PUBLIC", raising=False)
    monkeypatch.delenv("REQUIRE_ALLOWLIST", raising=False)
    monkeypatch.setattr(bot, "build_application", lambda _t: object())
    with pytest.raises(SystemExit, match="fail-closed"):
        bot.main()


def test_f17_cooldown_lengkap_dan_semaphore_global():
    for cmd in ("berita", "fundamental", "skor", "beli", "jual", "agenda", "jejak"):
        assert cmd in guards._COOLDOWN_SECONDS
    sem = guards.global_heavy_semaphore()
    assert sem is not None


def test_f19_download_model_verify_sha256(tmp_path):
    f = tmp_path / "config.json"
    f.write_bytes(b"hello")
    assert download_model.verify_sha256(str(f), download_model.sha256_file(str(f))) is True
    assert download_model.verify_sha256(str(f), "0" * 64) is False


def test_f20_rss_tolak_doctype_dan_batas_ukuran():
    with pytest.raises(ValueError, match="DOCTYPE"):
        news._safe_parse_xml(b'<!DOCTYPE foo [<!ENTITY xxe "boom">]><rss/>')
    root = news._safe_parse_xml(b"<rss><channel></channel></rss>")
    assert root.tag == "rss"
    assert news.MAX_RSS_BYTES == 2 * 1024 * 1024


def test_f23_normalize_ticker_tolak_unicode_dan_terlalu_panjang():
    assert data.normalize_ticker("bbca") == "BBCA.JK"
    with pytest.raises(ValueError):
        data.normalize_ticker("BBCA²")
    with pytest.raises(ValueError):
        data.normalize_ticker("ABCDEFGHIJK")


def test_f24_pricing_expiry_warning():
    assert pricing.expiry_warning(dt.date(2026, 11, 24)) is None
    w_soon = pricing.expiry_warning(dt.date(2026, 12, 20))
    assert w_soon is not None and "2026-12-31" in w_soon
    w_exp = pricing.expiry_warning(dt.date(2027, 1, 2))
    assert w_exp is not None and "berakhir" in w_exp

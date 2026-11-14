"""Tes offline handler /cek: rangkaian fetcher dipalsukan, tanpa jaringan."""
from __future__ import annotations

import asyncio
import types

import numpy as np
import pytest

import bot
from core import banding, data, fundamental, news, store
from tests.conftest import make_prices


class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self

    async def reply_photo(self, **kw):  # pragma: no cover
        return self


class _Chat:
    id = 100


class _User:
    id = 100


class _Update:
    def __init__(self, text):
        self.effective_message = _Msg(text)
        self.effective_chat = _Chat()
        self.effective_user = _User()


def _res(close=1000.0):
    ind = make_prices(np.linspace(900, close, 80), volume=10_000_000)
    ind["atr14"] = 30.0
    return {"ticker": "BBCA", "snap": {"close": close, "rsi": 55.0,
                                       "ret20d": 2.0},
            "score": {"pct": 35.0}, "ind": ind, "stale_note": ""}


@pytest.fixture
def jalankan(monkeypatch, tmp_store):
    async def fake_fetch(t):
        return _res()

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals",
                        lambda t: {"per": 12.0, "pbv": 1.4, "roe": 18.0,
                                   "sector": "Financials"})
    monkeypatch.setattr(fundamental, "score_fundamentals",
                        lambda raw: {"pct": 20.0, "label": "BAIK"})
    monkeypatch.setattr(news, "fetch_news", lambda t: [{"title": "uji"}])
    monkeypatch.setattr(news, "news_sentiment",
                        lambda items: {"pct": 25.0, "n": 1, "label": "POSITIF",
                                       "details": [{"title": "Laba naik"}]})
    monkeypatch.setattr(data, "fetch_weekly",
                        lambda t: make_prices(np.linspace(900, 1000, 60)))
    monkeypatch.setattr(bot, "_cek_bandar_row",
                        lambda t: {"ForeignNet": 4_000_000_000})
    monkeypatch.setattr(bot, "_cek_agenda", lambda t: (None, None))
    monkeypatch.setattr(banding, "sector_context",
                        lambda *a, **k: {"ret20d": 1.0, "sector": "Financials"})
    monkeypatch.setattr(store, "get_watchlist", lambda cid: [])
    bot._last_used.clear()

    def run(text):
        upd = _Update(text)
        asyncio.run(bot.cmd_cek(upd, types.SimpleNamespace()))
        return upd.effective_message.replies

    return run


def _teks(replies):
    return "\n".join(t for t, _ in replies)


def test_cek_enam_bagian_dan_kesimpulan(jalankan):
    teks = _teks(jalankan("/cek BBCA"))
    for label in ("Tren", "Valuasi vs sektor", "Aliran asing", "Berita",
                  "Agenda 14 hari", "Likuiditas"):
        assert label in teks, label
    assert "CEK SEBELUM BELI — BBCA" in teks
    assert "Kesimpulan" in teks
    assert "LAYAK" in teks or "TUNGGU" in teks


def test_cek_asing_kosong_ditulis_jujur(jalankan, monkeypatch):
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda t: None)
    teks = _teks(jalankan("/cek BBCA"))
    assert "tidak tersedia" in teks
    assert "Tidak tersedia: aliran asing" in teks


def test_cek_tanpa_argumen_memberi_contoh(jalankan):
    teks = _teks(jalankan("/cek"))
    assert "/cek BBCA" in teks


def test_cek_kode_salah_tidak_lanjut(jalankan):
    teks = _teks(jalankan("/cek 123"))
    assert "tidak valid" in teks


def test_cek_data_harga_gagal_dijawab_ramah(jalankan, monkeypatch):
    async def boom(t):
        raise ValueError("Data untuk 'XXXX' tidak ditemukan")

    monkeypatch.setattr(bot, "fetch_and_analyze", boom)
    teks = _teks(jalankan("/cek XXXX"))
    assert "tidak ditemukan" in teks

def test_cek_mengingatkan_posisi_yang_melewati_batas(jalankan, tmp_store):
    store.set_modal(100, 10_000_000)
    store.add_real_position(100, "BBCA", 30, 1000, "2026-11-12",
                            "Financials")
    teks = _teks(jalankan("/cek BBCA"))
    assert "jangan tambah" in teks
    assert "batas 25%" in teks

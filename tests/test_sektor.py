"""Tes offline P18: parser sektor BEI + handler /sektor."""
from __future__ import annotations

import asyncio
import types

import pytest

import bot
from core import bandar, sektor


# ---------------------------------------------------------------- core
def test_pct_dari_field_persen():
    assert sektor.pct_change({"IndexPercent": 1.25}) == 1.25


def test_pct_dihitung_dari_close_dan_previous():
    row = {"IndexClose": 1010.0, "IndexPrevious": 1000.0}
    assert sektor.pct_change(row) == pytest.approx(1.0)


def test_pct_dari_perubahan_poin():
    row = {"IndexClose": 1010.0, "IndexChange": 10.0}
    assert sektor.pct_change(row) == pytest.approx(1.0, abs=0.01)


def test_pct_tidak_menebak_tanpa_angka():
    assert sektor.pct_change({"IndexCode": "IDXENERGY"}) is None


def test_sector_rows_membuang_indeks_luas_dan_mengurutkan():
    rows = [
        {"IndexCode": "COMPOSITE", "IndexPercent": 0.4},
        {"IndexCode": "LQ45", "IndexPercent": 0.9},
        {"IndexCode": "IDXENERGY", "IndexPercent": 2.1},
        {"IndexCode": "IDXFINANCE", "IndexPercent": -0.8,
         "Date": "2026-11-12"},
        {"IndexCode": "IDXTRANS"},                    # tanpa angka: dibuang
        {"IndexCode": "IDXFINANCE", "IndexPercent": 9},  # duplikat: diabaikan
    ]
    out = sektor.sector_rows(rows)
    assert [s["code"] for s in out] == ["IDXENERGY", "IDXFINANCE"]
    assert out[0]["name"] == "Energi"
    assert out[1]["pct"] == -0.8


def test_summarize_tanpa_sektor_tidak_dianggap_nol():
    s = sektor.summarize([{"IndexCode": "COMPOSITE", "IndexPercent": 0.5}])
    assert s["ok"] is False
    assert "tidak" in s["reason"]
    assert s["sectors"] == []


def test_summarize_menyebut_terkuat_dan_terlemah():
    s = sektor.summarize([
        {"IndexCode": "IDXENERGY", "IndexPercent": 2.0},
        {"IndexCode": "IDXFINANCE", "IndexPercent": 0.5},
        {"IndexCode": "IDXTRANS", "IndexPercent": -1.5},
    ])
    assert s["ok"] is True
    assert s["best"]["code"] == "IDXENERGY"
    assert s["worst"]["code"] == "IDXTRANS"
    assert (s["n_positive"], s["n_negative"]) == (2, 1)


# -------------------------------------------------------------- handler
class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self


class _Chat:
    id = 100

    async def send_action(self, *a, **k):
        return None


class _User:
    id = 100


class _Update:
    def __init__(self):
        self.effective_message = _Msg("/sektor")
        self.effective_chat = _Chat()
        self.effective_user = _User()


ROWS = [
    {"IndexCode": "COMPOSITE", "IndexPercent": 0.3},
    {"IndexCode": "IDXENERGY", "IndexPercent": 2.1},
    {"IndexCode": "IDXFINANCE", "IndexPercent": -0.8},
]


def _run(monkeypatch, fake):
    monkeypatch.setattr(bandar, "get_index_summary", fake)
    bot._last_used.clear()
    upd = _Update()
    asyncio.run(bot.cmd_sektor(upd, types.SimpleNamespace()))
    return "\n".join(upd.effective_message.replies)


def test_handler_menampilkan_sektor_terkuat(monkeypatch):
    teks = _run(monkeypatch, lambda: ROWS)
    assert "KEKUATAN SEKTOR" in teks
    assert "Energi" in teks and "+2,10%" in teks
    assert "Terkuat" in teks and "Terlemah" in teks
    assert "bukan rekomendasi" in teks


def test_handler_jujur_saat_payload_tanpa_sektor(monkeypatch):
    teks = _run(monkeypatch, lambda: [{"IndexCode": "COMPOSITE"}])
    assert "tidak bisa dibaca" in teks
    assert "tidak ada angka" in teks.lower()


def test_handler_jujur_saat_cloudflare(monkeypatch):
    def boom():
        raise bandar.IDXTidakTersedia("403")

    teks = _run(monkeypatch, boom)
    assert "diblokir Cloudflare" in teks
    assert "/sinyal beli bagus" in teks


def test_handler_jujur_saat_error_lain(monkeypatch):
    def boom():
        raise RuntimeError("jaringan putus")

    teks = _run(monkeypatch, boom)
    assert "tidak tersedia" in teks

"""Tes offline P15: penanda kualitas fundamental + /sinyal beli bagus."""
from __future__ import annotations

import asyncio
import types

import pytest

import bot
from core import kualitas, store


# ------------------------------------------------------------ core.kualitas
def test_judge_sehat():
    k = kualitas.judge({"per": 12.0, "pbv": 1.4, "roe": 18.0})
    assert k["ok"] is True
    assert k["label"] == "sehat"
    assert any("ROE" in b for b in k["bits"])


def test_judge_merugi_lebih_kuat_dari_valuasi():
    k = kualitas.judge({"per": -5.0, "pbv": 1.0, "roe": -3.0})
    assert k["ok"] is False and k["label"] == "merugi"


def test_judge_roe_tipis_dan_mahal():
    assert kualitas.judge({"per": 10.0, "pbv": 1.0, "roe": 3.0})["label"] == \
        "ROE tipis"
    assert kualitas.judge({"per": 40.0, "pbv": 5.0, "roe": 20.0})["label"] == \
        "mahal"


def test_judge_tanpa_data_bukan_lolos_bukan_tolak():
    k = kualitas.judge(None)
    assert k["ok"] is None
    assert kualitas.passes(k) is False


def test_cache_harian_bolak_balik(tmp_path):
    assert kualitas.load_cached(str(tmp_path), "BBCA", "2026-11-12") is None
    assert kualitas.save_cached(str(tmp_path), "BBCA", "2026-11-12",
                                {"per": 12.0, "roe": 18.0})
    got = kualitas.load_cached(str(tmp_path), "BBCA", "2026-11-12")
    assert got["per"] == 12.0
    # tanggal lain = cache terpisah
    assert kualitas.load_cached(str(tmp_path), "BBCA", "2026-11-13") is None


# ------------------------------------------------------------------ handler
class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self

    async def reply_document(self, **kw):  # pragma: no cover
        return self


class _Chat:
    id = 100

    async def send_action(self, *a, **k):
        return None


class _User:
    id = 100


class _Update:
    def __init__(self, text):
        self.effective_message = _Msg(text)
        self.effective_chat = _Chat()
        self.effective_user = _User()


def _row(ticker, signal="BELI", rel=90.0, price=1000.0):
    return {"ticker": ticker, "name": f"PT {ticker}", "board": "Utama",
            "trade_date": "2026-11-11", "price": price, "rel_pct": rel,
            "rsi": 55.0, "ret20d": 2.0, "signal": signal, "reason": "",
            "entry": price, "sl": price * 0.97, "tp": price * 1.06}


@pytest.fixture
def sinyal(monkeypatch, tmp_store):
    rows = [_row("BBCA", rel=95), _row("TLKM", rel=90),
            _row("ANTM", rel=85), _row("GOTO", rel=80),
            _row("ZZZZ", rel=10, signal="JUAL")]
    monkeypatch.setattr(store, "load_market_scan", lambda sig=None: (
        [r for r in rows if r["signal"] == sig] if sig else list(rows)))
    monkeypatch.setattr(store, "market_scan_meta",
                        lambda: {"ok": 900, "trade_date": "2026-11-11"})

    async def fake_kval(tickers):
        table = {
            "BBCA": {"ok": True, "label": "sehat",
                     "bits": ["PER 12,0", "ROE 18,0%"], "reason": ""},
            "TLKM": {"ok": False, "label": "mahal",
                     "bits": ["PER 30,0"], "reason": "PER/PBV di atas ambang"},
            "ANTM": {"ok": None, "label": "belum dinilai", "bits": [],
                     "reason": "data fundamental tidak tersedia"},
            "GOTO": {"ok": True, "label": "sehat", "bits": ["ROE 9,0%"],
                     "reason": ""},
        }
        return {t: table[t] for t in tickers if t in table}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    bot._last_used.clear()

    def run(text):
        upd = _Update(text)
        asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
        return upd.effective_message.replies

    return run


def _teks(replies):
    return "\n".join(replies)


def test_sinyal_beli_bagus_hanya_yang_lolos(sinyal):
    teks = _teks(sinyal("/sinyal beli bagus"))
    assert "BELI BERKUALITAS" in teks
    assert "BBCA" in teks and "GOTO" in teks
    # TLKM gagal saringan, ANTM belum dinilai -> tidak di daftar utama
    assert "Tidak lolos: TLKM" in teks
    assert "belum dinilai" in teks


def test_sinyal_beli_biasa_memberi_penanda(sinyal):
    teks = _teks(sinyal("/sinyal beli"))
    assert "BBCA" in teks
    assert "✅ sehat" in teks
    assert "fundamental:" in teks


def test_sinyal_detail_menyebut_kualitas(sinyal):
    teks = _teks(sinyal("/sinyal BBCA"))
    assert "Kualitas fundamental" in teks
    assert "sehat" in teks


def test_sinyal_beli_bagus_tanpa_kandidat(monkeypatch, tmp_store):
    monkeypatch.setattr(store, "load_market_scan", lambda sig=None: [
        _row("ZZZZ", rel=5, signal="JUAL")])
    monkeypatch.setattr(store, "market_scan_meta", lambda: {"ok": 900})
    bot._last_used.clear()
    upd = _Update("/sinyal beli bagus")
    asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
    teks = _teks(upd.effective_message.replies)
    assert "Tidak ada kandidat BELI" in teks

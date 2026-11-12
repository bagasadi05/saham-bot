"""Tes offline: ringkasan /sinyal ringkas + tombol navigasi `sg:*`.

Ringkasan harus bisa dibaca tanpa kamus: kata biasa, satu peringatan,
tanpa rel/RSI/entry/fundamental. Angka detail pindah ke tampilan rincian
yang dibuka lewat tombol.
"""
from __future__ import annotations

import asyncio
import types

import bot
from core import store
from handlers import sinyal as sinyal_mod


class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []
        self.docs: list[dict] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self

    async def edit_text(self, text, **kw):
        self.edits.append((text, kw))
        return self

    async def reply_document(self, **kw):
        self.docs.append(kw)
        return self


class _Chat:
    id = 100

    async def send_action(self, *a, **k):
        return None


class _User:
    id = 100


class _Query:
    def __init__(self, data):
        self.data = data
        self.from_user = _User()
        self.message = _Msg()
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True


class _Update:
    def __init__(self, text="", query=None):
        self.effective_message = query.message if query else _Msg(text)
        self.effective_chat = _Chat()
        self.effective_user = _User()
        self.callback_query = query


def _row(ticker, signal="BELI", rel=90.0, price=1000.0, ret20d=2.0):
    return {"ticker": ticker, "name": f"PT {ticker}", "board": "Utama",
            "trade_date": "2026-11-11", "price": price, "rel_pct": rel,
            "rsi": 55.0, "ret20d": ret20d, "signal": signal, "reason": "",
            "entry": price, "sl": price * 0.97, "tp": price * 1.06,
            "ret1d": 0.5, "ret5d": 1.0, "sma20": price, "sma50": price,
            "sma200": price}


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard
            for b in row]


def _seed(monkeypatch):
    rows = [_row("BBCA", rel=95, price=8750, ret20d=3.2),
            _row("TLKM", rel=90, price=3000, ret20d=1.5),
            _row("ANTM", rel=85, price=1500, ret20d=-8.0, signal="JUAL"),
            _row("GOTO", rel=80, price=90, ret20d=-2.0, signal="JUAL")]
    monkeypatch.setattr(store, "load_market_scan", lambda: list(rows))
    monkeypatch.setattr(store, "market_scan_meta",
                        lambda: {"ok": 4, "trade_date": "2026-11-11",
                                 "scanned_at": "2026-11-13T00:09:00"})

    async def fake_kval(tickers):
        table = {
            "BBCA": {"ok": False, "label": "ROE tipis", "bits": [],
                     "reason": ""},
            "TLKM": {"ok": False, "label": "merugi", "bits": [],
                     "reason": ""},
        }
        return {t: table[t] for t in tickers if t in table}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    bot._last_used.clear()
    return rows


# ------------------------------------------------------------- kata biasa
def test_peringatan_diterjemahkan_ke_kata_biasa():
    assert sinyal_mod._peringatan({"ok": False, "label": "merugi"}) == \
        "perusahaan sedang merugi"
    assert sinyal_mod._peringatan({"ok": False, "label": "ROE tipis"}) == \
        "laba tipis"
    assert sinyal_mod._peringatan({"ok": False, "label": "mahal"}) == \
        "harga sudah mahal"
    assert sinyal_mod._peringatan({"ok": True, "label": "sehat"}) == ""
    assert sinyal_mod._peringatan({"ok": None, "label": "belum dinilai"}) == ""
    assert sinyal_mod._peringatan(None) == ""


def test_baris_ringkas_tanpa_jargon():
    naik = sinyal_mod._sinyal_row_ringkas(1, _row("BBCA", price=8750,
                                                  ret20d=3.2))
    assert "Rp8.750" in naik and "naik 3,2% sebulan terakhir" in naik
    assert "rel" not in naik and "RSI" not in naik
    assert "entry" not in naik and "fundamental" not in naik
    turun = sinyal_mod._sinyal_row_ringkas(
        1, _row("ANTM", price=1500, ret20d=-8.0, signal="JUAL"),
        {"ok": False, "label": "merugi"})
    assert "turun 8,0%" in turun
    assert "perusahaan sedang merugi" in turun


# ------------------------------------------------------------- perintah
def test_overview_ringkas_dan_punya_tombol(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update("/sinyal")
    asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
    text, kw = upd.effective_message.replies[-1]
    assert "SINYAL PASAR IDX" in text
    assert "layak dilirik" in text and "sebaiknya dihindari" in text
    assert "laba tipis" in text and "perusahaan sedang merugi" in text
    assert "bukan jaminan" in text
    assert "rel " not in text and "RSI" not in text
    assert "Rincian:" not in text and "20h" not in text
    assert "fundamental:" not in text and "entry" not in text
    cbs = _callbacks(kw["reply_markup"])
    assert cbs == ["sg:buy", "sg:sell", "sg:good", "pick:sinyal_detail",
                   "sg:csv", "sg:scan"]


def test_section_punya_tombol_kembali(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update("/sinyal beli")
    asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
    text, kw = upd.effective_message.replies[-1]
    assert "PERINGKAT ATAS" in text
    cbs = _callbacks(kw["reply_markup"])
    assert "sg:ov" in cbs and "sg:scan" in cbs and "sg:good" in cbs


def test_detail_punya_tombol_analisis(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update("/sinyal BBCA")
    asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
    text, kw = upd.effective_message.replies[-1]
    assert "Entry 8,750" in text and "/analisis BBCA" in text
    cbs = _callbacks(kw["reply_markup"])
    assert "sg:an:BBCA" in cbs and "sg:ov" in cbs


# ------------------------------------------------------------- navigasi
def test_nav_ringkasan_edit_di_tempat(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update(query=_Query("sg:ov"))
    asyncio.run(bot.menu_callback(
        upd, types.SimpleNamespace(job_queue=None)))
    text, kw = upd.effective_message.edits[-1]
    assert "SINYAL PASAR IDX" in text and "layak dilirik" in text
    assert "sg:buy" in _callbacks(kw["reply_markup"])


def test_nav_beli_kirim_pesan_baru(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update(query=_Query("sg:buy"))
    asyncio.run(bot.menu_callback(
        upd, types.SimpleNamespace(job_queue=None)))
    assert "Memuat" in upd.effective_message.edits[-1][0]
    text, kw = upd.effective_message.replies[-1]
    assert "PERINGKAT ATAS" in text
    assert "sg:ov" in _callbacks(kw["reply_markup"])


def test_nav_tanpa_snapshot_arahkan_scan(monkeypatch, tmp_store):
    monkeypatch.setattr(store, "load_market_scan", lambda: [])
    monkeypatch.setattr(store, "market_scan_meta", lambda: {})
    upd = _Update(query=_Query("sg:ov"))
    asyncio.run(bot.menu_callback(
        upd, types.SimpleNamespace(job_queue=None)))
    text, kw = upd.effective_message.edits[-1]
    assert "Belum ada" in text
    assert "sg:scan" in _callbacks(kw["reply_markup"])


def test_nav_csv_kirim_dokumen(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update(query=_Query("sg:csv"))
    asyncio.run(bot.menu_callback(
        upd, types.SimpleNamespace(job_queue=None)))
    assert upd.effective_message.docs
    assert upd.effective_message.docs[0]["filename"] == "sinyal_idx.csv"


def test_picker_detail_saham_dari_tombol(monkeypatch, tmp_store):
    _seed(monkeypatch)
    upd = _Update(query=_Query("pick:sinyal_detail"))
    asyncio.run(bot.menu_callback(
        upd, types.SimpleNamespace(job_queue=None)))
    text, kw = upd.effective_message.edits[-1]
    assert "Detail sinyal" in text
    assert any(c.startswith("tick:sinyal_detail:")
               for c in _callbacks(kw["reply_markup"]))

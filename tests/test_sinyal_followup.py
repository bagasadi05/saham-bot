"""Tes offline follow-up `/sinyal` & navigasi `sg:*` (modal, 1-tap, Menu Utama)."""
from __future__ import annotations

import asyncio
import types

import bot
from core import store
from handlers import sinyal as sinyal_mod


class _Msg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []
        self.docs: list[dict] = []

    async def reply_text(self, text: str, **kw):
        self.replies.append((text, kw))
        return self

    async def edit_text(self, text: str, **kw):
        self.edits.append((text, kw))
        return self

    async def reply_document(self, **kw):
        self.docs.append(kw)
        return self


class _Chat:
    def __init__(self, cid: int = 100):
        self.id = cid

    async def send_action(self, *a, **k):
        return None


class _User:
    def __init__(self, uid: int = 100):
        self.id = uid


class _Query:
    def __init__(self, data: str, uid: int = 100):
        self.data = data
        self.from_user = _User(uid)
        self.message = _Msg()
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True


class _Update:
    def __init__(self, text: str = "", query: _Query | None = None, cid: int = 100):
        self.effective_message = query.message if query else _Msg(text)
        self.effective_chat = _Chat(cid)
        self.effective_user = _User(cid)
        self.callback_query = query


def _row(
    ticker: str,
    *,
    signal: str = "BELI",
    rel: float = 90.0,
    price: float | None = 1000.0,
    ret20d: float = 2.0,
):
    px = price if price is not None else 0.0
    return {
        "ticker": ticker,
        "name": f"PT {ticker}",
        "board": "Utama",
        "trade_date": "2026-11-11",
        "price": price,
        "rel_pct": rel,
        "rsi": 55.0,
        "ret20d": ret20d,
        "signal": signal,
        "reason": "",
        "entry": px if price else None,
        "sl": px * 0.97 if price else None,
        "tp": px * 1.06 if price else None,
        "ret1d": 0.5,
        "ret5d": 1.0,
        "sma20": px,
        "sma50": px,
        "sma200": px,
    }


def _cbs(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _assert_callbacks_valid(markup):
    cbs = _cbs(markup)
    assert cbs
    for cb in cbs:
        assert isinstance(cb, str) and cb
        assert len(cb.encode("utf-8")) <= 64


def _seed_market(monkeypatch):
    rows = [
        _row("MPMX", rel=96, price=1000),
        _row("LPIN", rel=94, price=2000),
        _row("BBCA", rel=92, price=8750),
        _row("BBRI", rel=90, price=4000),
        _row("BMRI", rel=88, price=5000),
        _row("ASII", rel=86, price=4800),
        _row("TLKM", rel=84, price=3000),
        _row("NFCX", rel=82, price=1500),
        _row("INDF", rel=80, price=6500),
        _row("ANTM", rel=5, signal="JUAL", price=1500, ret20d=-8.0),
        _row("GOTO", rel=8, signal="JUAL", price=90, ret20d=-4.0),
        _row("BRMS", rel=12, signal="JUAL", price=350, ret20d=-3.0),
        _row("BUKA", rel=15, signal="JUAL", price=120, ret20d=-2.0),
        _row("UNVR", rel=18, signal="JUAL", price=2200, ret20d=-1.5),
    ]
    monkeypatch.setattr(store, "load_market_scan", lambda: list(rows))
    monkeypatch.setattr(
        store,
        "market_scan_meta",
        lambda: {"ok": len(rows), "trade_date": "2026-11-11", "scanned_at": "2026-11-13T00:09:00"},
    )

    async def fake_kval(tickers):
        table = {
            "MPMX": {"ok": True, "label": "sehat", "bits": ["PER 8,0", "ROE 15,0%"], "reason": ""},
            "LPIN": {"ok": True, "label": "sehat", "bits": ["PER 9,0", "ROE 12,0%"], "reason": ""},
            "BBCA": {"ok": True, "label": "sehat", "bits": ["PER 12,0", "ROE 18,0%"], "reason": ""},
            "BBRI": {"ok": True, "label": "sehat", "bits": ["PER 11,0", "ROE 17,0%"], "reason": ""},
            "BMRI": {"ok": True, "label": "sehat", "bits": ["PER 10,0", "ROE 19,0%"], "reason": ""},
            "ASII": {"ok": True, "label": "sehat", "bits": ["PER 9,5", "ROE 14,0%"], "reason": ""},
            "INDF": {"ok": True, "label": "sehat", "bits": ["PER 11,0", "ROE 16,0%"], "reason": ""},
            "TLKM": {"ok": False, "label": "mahal", "bits": ["PER 30,0"], "reason": "PER/PBV di atas ambang"},
            "NFCX": {"ok": None, "label": "belum dinilai", "bits": [], "reason": ""},
        }
        return {t: table[t] for t in tickers if t in table}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    bot._last_used.clear()
    return rows


# --------------------------------------------------------- (1) BELI BERKUALITAS
def test_beli_bagus_tampilkan_modal_dan_biaya_1_lot_serta_tombol_maks_6(monkeypatch, tmp_store):
    _seed_market(monkeypatch)
    store.set_modal(100, 500_000)

    upd = _Update(query=_Query("sg:good", uid=100))
    asyncio.run(bot.menu_callback(upd, types.SimpleNamespace(job_queue=None)))

    text, kw = upd.effective_message.replies[-1]
    assert "Modal tersimpan: <b>Rp500.000</b>" in text
    assert "Rp125.000" in text
    # MPMX 1000 * 100 * 1.0015 = Rp100.150 (muat slot 25% & modal)
    assert "Rp100.150" in text and "cukup slot 25%" in text
    # LPIN 2000 * 100 * 1.0015 = Rp200.300 (> slot 25%, muat modal penuh)
    assert "Rp200.300" in text and "cukup modal penuh" in text
    # BBCA 8750 * 100 * 1.0015 = Rp876.313 (> modal Rp500.000)
    assert "Rp876.313" in text and "kurang Rp376.313" in text

    kb = kw["reply_markup"]
    _assert_callbacks_valid(kb)
    cbs = _cbs(kb)
    # 7 sehat (MPMX, LPIN, BBCA, BBRI, BMRI, ASII, INDF) -> maks 6 tombol per kandidat + sg:buy + sg:ov + menu
    assert cbs == [
        "tick:cek:MPMX", "tick:rencana:MPMX",
        "tick:cek:LPIN", "tick:rencana:LPIN",
        "tick:cek:BBCA", "tick:rencana:BBCA",
        "tick:cek:BBRI", "tick:rencana:BBRI",
        "tick:cek:BMRI", "tick:rencana:BMRI",
        "tick:cek:ASII", "tick:rencana:ASII",
        "sg:buy", "sg:ov", "menu",
    ]
    assert "tick:cek:INDF" not in cbs
    assert "tick:cek:TLKM" not in cbs
    assert "tick:cek:NFCX" not in cbs


def test_beli_bagus_kompatibel_caller_lama_dan_harga_kosong(monkeypatch, tmp_store):
    rows = [_row("TEST", rel=95, price=None)]
    kval = {"TEST": {"ok": True, "label": "sehat", "bits": ["ROE 12,0%"], "reason": ""}}
    meta = {"ok": 1, "trade_date": "2026-11-11"}

    # Caller lama tanpa argumen modal
    txt = sinyal_mod._beli_bagus_text(rows, meta, bot, kval)
    assert "BELI BERKUALITAS" in txt
    assert "harga tidak tersedia" in txt
    assert "Modal tersimpan" not in txt

    # bagus_markup() tanpa argumen tetap kompatibel
    kb = sinyal_mod.bagus_markup()
    assert _cbs(kb) == ["sg:buy", "sg:ov"]


def test_cmd_sinyal_beli_bagus_punya_tombol_tanpa_home_jika_bukan_menu(monkeypatch, tmp_store):
    _seed_market(monkeypatch)
    store.set_modal(100, 1_000_000)
    upd = _Update("/sinyal beli bagus")
    asyncio.run(bot.cmd_sinyal(upd, types.SimpleNamespace()))
    text, kw = upd.effective_message.replies[-1]
    assert "Rp1.000.000" in text
    cbs = _cbs(kw["reply_markup"])
    assert "tick:cek:MPMX" in cbs and "tick:rencana:MPMX" in cbs
    assert "menu" not in cbs

    # Dari run:sinyal_bagus (from_menu=True) wajib ada menu
    bot._last_used.clear()
    upd_menu = _Update(query=_Query("run:sinyal_bagus", uid=100))
    asyncio.run(bot.menu_callback(upd_menu, types.SimpleNamespace(job_queue=None)))
    cbs_menu = _cbs(upd_menu.effective_message.replies[-1][1]["reply_markup"])
    assert "menu" in cbs_menu and cbs_menu.count("menu") == 1


# --------------------------------------------------------- (2) SECTION BELI & JUAL
def test_section_markup_beli_dan_jual_top4_serta_nav(monkeypatch, tmp_store):
    _seed_market(monkeypatch)

    # BELI lewat nav sg:buy
    upd_buy = _Update(query=_Query("sg:buy"))
    asyncio.run(bot.menu_callback(upd_buy, types.SimpleNamespace(job_queue=None)))
    txt_buy, kw_buy = upd_buy.effective_message.replies[-1]
    assert "PERINGKAT ATAS" in txt_buy
    assert "Ketuk tombol saham" in txt_buy
    kb_buy = kw_buy["reply_markup"]
    _assert_callbacks_valid(kb_buy)
    assert _cbs(kb_buy) == [
        "tick:cek:MPMX", "tick:rencana:MPMX",
        "tick:cek:LPIN", "tick:rencana:LPIN",
        "tick:cek:BBCA", "tick:rencana:BBCA",
        "tick:cek:BBRI", "tick:rencana:BBRI",
        "sg:good", "sg:ov", "sg:scan", "menu",
    ]

    # JUAL lewat nav sg:sell (diurutkan rel_pct terendah: ANTM 5, GOTO 8, BRMS 12, BUKA 15; UNVR 18 terpotong maks 4)
    upd_sell = _Update(query=_Query("sg:sell"))
    asyncio.run(bot.menu_callback(upd_sell, types.SimpleNamespace(job_queue=None)))
    txt_sell, kw_sell = upd_sell.effective_message.replies[-1]
    assert "PERINGKAT BAWAH" in txt_sell
    assert "Ketuk tombol saham" in txt_sell
    kb_sell = kw_sell["reply_markup"]
    _assert_callbacks_valid(kb_sell)
    assert _cbs(kb_sell) == [
        "tick:jual:ANTM", "tick:analisis:ANTM",
        "tick:jual:GOTO", "tick:analisis:GOTO",
        "tick:jual:BRMS", "tick:analisis:BRMS",
        "tick:jual:BUKA", "tick:analisis:BUKA",
        "sg:ov", "sg:scan", "menu",
    ]
    assert "tick:jual:UNVR" not in _cbs(kb_sell)

    # /sinyal jual langsung (from_menu=False): tombol kandidat tetap ada, tanpa menu
    bot._last_used.clear()
    upd_cmd = _Update("/sinyal jual")
    asyncio.run(bot.cmd_sinyal(upd_cmd, types.SimpleNamespace()))
    cbs_cmd = _cbs(upd_cmd.effective_message.replies[-1][1]["reply_markup"])
    assert "tick:jual:ANTM" in cbs_cmd and "tick:analisis:ANTM" in cbs_cmd
    assert "menu" not in cbs_cmd

    # from_menu=True langsung di section_markup tanpa duplikasi saat _with_home
    kb_direct = sinyal_mod._with_home(
        sinyal_mod.section_markup("BELI", ["BBCA", "BBCA", "tlkm.jk"], from_menu=True),
        True,
    )
    assert _cbs(kb_direct).count("menu") == 1
    assert "tick:cek:BBCA" in _cbs(kb_direct)
    assert "tick:cek:TLKM" in _cbs(kb_direct)


# --------------------------------------------------------- (3) DETAIL MARKUP
def test_detail_markup_non_jual_vs_jual_sanitasi_dan_batas_64_byte(monkeypatch, tmp_store):
    kb_beli = sinyal_mod.detail_markup(" bbca.jk ", sig="BELI")
    _assert_callbacks_valid(kb_beli)
    assert _cbs(kb_beli) == [
        "tick:cek:BBCA",
        "tick:rencana:BBCA",
        "exec:buy:BBCA",
        "tick:jual:BBCA",
        "sg:an:BBCA",
        "sg:ov",
    ]
    assert "🛡️ Cek 6 Poin" in _labels(kb_beli)
    assert "🎯 Hitung Lot" in _labels(kb_beli)

    kb_tahan = sinyal_mod.detail_markup("TLKM", sig="TAHAN")
    assert _cbs(kb_tahan)[:2] == ["tick:cek:TLKM", "tick:rencana:TLKM"]

    kb_jual = sinyal_mod.detail_markup("ANTM", sig="JUAL")
    _assert_callbacks_valid(kb_jual)
    assert _cbs(kb_jual) == ["tick:jual:ANTM", "exec:sell:ANTM", "sg:an:ANTM", "sg:ov"]
    assert "tick:cek:ANTM" not in _cbs(kb_jual)
    assert "exec:buy:ANTM" not in _cbs(kb_jual)

    # Ticker panjang 10 karakter tetap <= 64 byte
    kb_long = sinyal_mod.detail_markup("ABCDEFGHIJ", sig="BELI", from_menu=True)
    _assert_callbacks_valid(kb_long)
    assert "menu" in _cbs(kb_long)

    # Detail lewat pick:sinyal_detail -> tick:sinyal_detail:MPMX (from_menu=True)
    _seed_market(monkeypatch)
    upd = _Update(query=_Query("tick:sinyal_detail:MPMX"))
    asyncio.run(bot.menu_callback(upd, types.SimpleNamespace(job_queue=None)))
    kb_nav_detail = upd.effective_message.replies[-1][1]["reply_markup"]
    _assert_callbacks_valid(kb_nav_detail)
    assert _cbs(kb_nav_detail) == [
        "tick:cek:MPMX",
        "tick:rencana:MPMX",
        "exec:buy:MPMX",
        "tick:jual:MPMX",
        "sg:an:MPMX",
        "sg:ov",
        "menu",
    ]


# --------------------------------------------------------- (4) OVERVIEW & NAV
def test_overview_tetap_6_callback_dan_semua_jalur_nav_punya_menu_utama(monkeypatch, tmp_store):
    _seed_market(monkeypatch)

    # /sinyal langsung: persis 6 callback tanpa menu
    upd_cmd = _Update("/sinyal")
    asyncio.run(bot.cmd_sinyal(upd_cmd, types.SimpleNamespace()))
    assert _cbs(upd_cmd.effective_message.replies[-1][1]["reply_markup"]) == [
        "sg:buy", "sg:sell", "sg:good", "pick:sinyal_detail", "sg:csv", "sg:scan",
    ]

    # sg:ov -> edit di tempat + menu
    upd_ov = _Update(query=_Query("sg:ov"))
    asyncio.run(bot.menu_callback(upd_ov, types.SimpleNamespace(job_queue=None)))
    assert _cbs(upd_ov.effective_message.edits[-1][1]["reply_markup"]) == [
        "sg:buy", "sg:sell", "sg:good", "pick:sinyal_detail", "sg:csv", "sg:scan", "menu",
    ]

    # sg:csv -> reply_document punya reply_markup dengan sg:ov + menu
    upd_csv = _Update(query=_Query("sg:csv"))
    asyncio.run(bot.menu_callback(upd_csv, types.SimpleNamespace(job_queue=None)))
    doc_kw = upd_csv.effective_message.docs[-1]
    assert _cbs(doc_kw["reply_markup"]) == ["sg:ov", "menu"]

    # sg:unknown fallback -> edit ke overview + menu
    upd_unk = _Update(query=_Query("sg:tidak_dikenal"))
    asyncio.run(bot.menu_callback(upd_unk, types.SimpleNamespace(job_queue=None)))
    assert _cbs(upd_unk.effective_message.edits[-1][1]["reply_markup"]) == [
        "sg:buy", "sg:sell", "sg:good", "pick:sinyal_detail", "sg:csv", "sg:scan", "menu",
    ]

    # sg:good saat tidak ada BELI -> tetap punya menu
    monkeypatch.setattr(
        store,
        "load_market_scan",
        lambda: [_row("ANTM", rel=10, signal="JUAL")],
    )
    upd_empty_buy = _Update(query=_Query("sg:good"))
    asyncio.run(bot.menu_callback(upd_empty_buy, types.SimpleNamespace(job_queue=None)))
    assert "menu" in _cbs(upd_empty_buy.effective_message.replies[-1][1]["reply_markup"])

    # Snapshot kosong -> empty_markup + menu
    monkeypatch.setattr(store, "load_market_scan", lambda: [])
    upd_empty = _Update(query=_Query("sg:ov"))
    asyncio.run(bot.menu_callback(upd_empty, types.SimpleNamespace(job_queue=None)))
    assert _cbs(upd_empty.effective_message.edits[-1][1]["reply_markup"]) == ["sg:scan", "menu"]


def test_nav_sg_an_sanitasi_ticker_dan_tolak_kosong(monkeypatch, tmp_store):
    _seed_market(monkeypatch)
    called: list[tuple[str, list[str]]] = []

    async def fake_run(update, action, tickers):
        called.append((action, tickers))

    monkeypatch.setattr(bot, "run_menu_action", fake_run)

    upd_ok = _Update(query=_Query("sg:an:bbca.jk"))
    asyncio.run(bot.menu_callback(upd_ok, types.SimpleNamespace(job_queue=None)))
    assert called == [("analisis", ["BBCA"])]

    called.clear()
    upd_bad = _Update(query=_Query("sg:an:!!!"))
    asyncio.run(bot.menu_callback(upd_bad, types.SimpleNamespace(job_queue=None)))
    assert not called
    assert "tidak valid" in upd_bad.effective_message.edits[-1][0]
    assert "menu" in _cbs(upd_bad.effective_message.edits[-1][1]["reply_markup"])


def test_edge_cases_lot_detail_kosong_dan_run_sinyal(monkeypatch, tmp_store):
    _seed_market(monkeypatch)
    # Presisi float: 1300 * 100 * 1.0015 = 130195 persis (bukan 130196)
    assert sinyal_mod._lot_cost_1(1300) == 130195

    # Ticker kosong/invalid di detail_markup tidak hasilkan callback cacat
    kb_empty = sinyal_mod.detail_markup("", sig="BELI", from_menu=True)
    assert _cbs(kb_empty) == ["sg:ov", "menu"]

    # run:sinyal (from_menu=True) tetap bawa 6 tombol overview + menu
    upd_run = _Update(query=_Query("run:sinyal"))
    asyncio.run(bot.menu_callback(upd_run, types.SimpleNamespace(job_queue=None)))
    assert _cbs(upd_run.effective_message.replies[-1][1]["reply_markup"]) == [
        "sg:buy", "sg:sell", "sg:good", "pick:sinyal_detail", "sg:csv", "sg:scan", "menu",
    ]

    # /sinyal BBCA.JK dibersihkan & cocok dengan BBCA di snapshot
    bot._last_used.clear()
    upd_jk = _Update("/sinyal BBCA.JK")
    asyncio.run(bot.cmd_sinyal(upd_jk, types.SimpleNamespace()))
    txt, kw = upd_jk.effective_message.replies[-1]
    assert "BBCA" in txt
    assert "tick:cek:BBCA" in _cbs(kw["reply_markup"])

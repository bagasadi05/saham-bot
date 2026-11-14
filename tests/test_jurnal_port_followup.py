"""Tes kontrak tombol tindak lanjut & guard handlers/portfolio, jurnal, dan analisis."""
from __future__ import annotations

import asyncio
import types

import pandas as pd
import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import bot
from core import banding, data, fundamental, guards, marketcal, news, store
from handlers import analisis as h_analisis
from handlers import jurnal as h_jurnal
from handlers import portfolio as h_portfolio
from tests.conftest import make_prices


class _FakeUser:
    def __init__(self, uid: int = 9001):
        self.id = uid


class _FakeChat:
    def __init__(self, cid: int = 9001):
        self.id = cid
        self.actions: list[str] = []

    async def send_action(self, action):
        self.actions.append(str(action))


class _FakeMessage:
    def __init__(self, chat_id: int = 9001, text: str = ""):
        self.chat_id = chat_id
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []
        self.photos: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def reply_photo(self, **kwargs):
        self.photos.append((kwargs.get("caption", ""), kwargs))
        return self


class _FakeQuery:
    def __init__(self, cb_data: str, uid: int = 9001, cid: int = 9001):
        self.data = cb_data
        self.from_user = _FakeUser(uid)
        self.message = _FakeMessage(chat_id=cid)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def _make_update(cid: int = 9001, text: str = "", *, query: _FakeQuery | None = None):
    msg = query.message if query is not None else _FakeMessage(chat_id=cid, text=text)
    return types.SimpleNamespace(
        effective_chat=_FakeChat(cid),
        effective_user=_FakeUser(cid),
        effective_message=msg,
        message=msg,
        callback_query=query,
    )


def _cb_list(markup) -> list[str]:
    assert markup is not None, "reply_markup tidak boleh None"
    out: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            cb = btn.callback_data
            if cb is not None:
                assert len(cb.encode("utf-8")) <= 64, f"callback >64 byte: {cb}"
                assert ".JK" not in cb.upper(), f".JK bocor di callback: {cb}"
                out.append(cb)
    return out


def _fake_ohlcv(price: float = 9000.0, rows: int = 60) -> pd.DataFrame:
    df = make_prices(rows, start=price * 0.9, drift=0.002)
    df.iloc[-1, df.columns.get_loc("close")] = price
    return df


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "bot.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / "store.json"))
    monkeypatch.setattr(store, "_ready_for", None)
    monkeypatch.setattr(bot, "TMP_DIR", str(tmp_path / "tmp"))
    guards._last_used.clear()
    guards.PENDING.clear()
    guards._pending_ts.clear()

    async def _always_ok(*args, **kwargs):
        return True

    monkeypatch.setattr(bot, "cooldown_ok", _always_ok)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. handlers/portfolio.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_menu", [False, True])
def test_portfolio_kosong_beri_tombol_pembuka(isolated_env, from_menu):
    upd = _make_update(cid=9101, text="/portofolio")
    asyncio.run(h_portfolio.do(upd, [], bot, from_menu=from_menu))
    assert upd.effective_message.replies
    _, kwargs = upd.effective_message.replies[-1]
    cbs = _cb_list(kwargs.get("reply_markup"))
    assert "guide:beli" in cbs
    assert "guide:cek" in cbs
    assert "sub:modal" in cbs
    assert "run:jurnal" in cbs
    assert "menu" in cbs


def test_portfolio_posisi_footer_dan_pick_jual_di_atas_4(isolated_env, monkeypatch):
    monkeypatch.setattr(
        data,
        "fetch_history",
        lambda sym, period="6mo", **kw: _fake_ohlcv(5000.0),
    )
    cid = 9102
    for idx, tk in enumerate(["BBCA", "BBRI", "BMRI", "TLKM"], start=1):
        store.record_execution_buy(cid, tk, 1, 9000.0 + idx * 100, "2026-11-14")

    upd_4 = _make_update(cid=cid, text="/portofolio")
    asyncio.run(h_portfolio.do(upd_4, [], bot, from_menu=False))
    _, kw_4 = upd_4.effective_message.replies[-1]
    cbs_4 = _cb_list(kw_4.get("reply_markup"))
    assert "pick:jual" not in cbs_4
    for expected in ("run:jurnal", "sub:modal", "run:portofolio", "menu"):
        assert expected in cbs_4

    store.record_execution_buy(cid, "ASII.JK", 1, 5200.0, "2026-11-14")
    upd_5 = _make_update(cid=cid, text="/portofolio")
    asyncio.run(h_portfolio.do(upd_5, [], bot, from_menu=False))
    _, kw_5 = upd_5.effective_message.replies[-1]
    cbs_5 = _cb_list(kw_5.get("reply_markup"))
    assert "pick:jual" in cbs_5
    for expected in ("run:jurnal", "sub:modal", "run:portofolio", "menu"):
        assert expected in cbs_5
    assert "tick:jual:ASII" in cbs_5


def test_portfolio_subcommands_selalu_punya_reply_markup(isolated_env):
    cid = 9103
    cases = [
        ["batas"],
        ["batas", "abc"],
        ["batas", "15", "30"],
        ["batas", "reset"],
        ["beli"],
        ["beli", "BBCA", "0", "9000"],
        ["beli", "BBCA", "2", "9000"],
        ["jual"],
        ["jual", "BBCA", "abc", "9500"],
        ["jual", "BBCA", "99", "9500"],
        ["jual", "BBCA", "1", "9500"],
        ["jual", "BBCA", "1", "9600"],
        ["riwayat"],
    ]
    for args in cases:
        upd = _make_update(cid=cid, text=f"/portofolio {' '.join(args)}")
        asyncio.run(h_portfolio.do(upd, args, bot, from_menu=False))
        assert upd.effective_message.replies, f"tidak ada reply untuk {args}"
        _, kw = upd.effective_message.replies[-1]
        cbs = _cb_list(kw.get("reply_markup"))
        assert "menu" in cbs

    upd_buy = _make_update(cid=cid, text="/portofolio beli TLKM 3 3000")
    asyncio.run(h_portfolio.do(upd_buy, ["beli", "TLKM", "3", "3000"], bot))
    buy_cbs = _cb_list(upd_buy.effective_message.replies[-1][1].get("reply_markup"))
    assert "tick:jual:TLKM" in buy_cbs
    assert "manual:alert_add" in buy_cbs
    assert "run:jurnal" in buy_cbs
    assert "run:portofolio" in buy_cbs

    upd_hist = _make_update(cid=cid, text="/portofolio riwayat")
    asyncio.run(h_portfolio.do(upd_hist, ["riwayat"], bot))
    hist_cbs = _cb_list(upd_hist.effective_message.replies[-1][1].get("reply_markup"))
    assert "run:portofolio" in hist_cbs
    assert "run:jurnal" in hist_cbs
    assert "menu" in hist_cbs


def test_portfolio_riwayat_kosong_tetap_punya_markup(isolated_env):
    upd = _make_update(cid=9104, text="/portofolio riwayat")
    asyncio.run(h_portfolio.do(upd, ["riwayat"], bot))
    _, kw = upd.effective_message.replies[-1]
    cbs = _cb_list(kw.get("reply_markup"))
    assert "run:portofolio" in cbs
    assert "run:jurnal" in cbs
    assert "menu" in cbs


# ---------------------------------------------------------------------------
# 2. handlers/jurnal.py
# ---------------------------------------------------------------------------


def test_confirm_exec_markup_unified_dan_aman_saat_allow_buy_false():
    for from_menu in (False, True):
        kb_buy = h_jurnal.confirm_exec_markup(
            "cek", "BBCA.JK", bot, from_menu=from_menu, allow_buy=True
        )
        cbs_buy = _cb_list(kb_buy)
        assert "exec:buy:BBCA" in cbs_buy
        assert "menu" in cbs_buy

        kb_no = h_jurnal.confirm_exec_markup(
            "cek", "BBCA.JK", bot, from_menu=from_menu, allow_buy=False
        )
        cbs_no = _cb_list(kb_no)
        assert not any(c.startswith("exec:buy:") for c in cbs_no)
        assert "tick:jual:BBCA" in cbs_no
        assert "menu" in cbs_no

    old_deps = types.SimpleNamespace(
        result_markup=lambda action, t: InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Catat Beli {t}", callback_data=f"exec:buy:{t}")],
            [InlineKeyboardButton("Menu", callback_data="menu")],
        ])
    )
    kb_safe = h_jurnal.confirm_exec_markup(
        "cek", "BBRI", old_deps, from_menu=False, allow_buy=False
    )
    cbs_safe = _cb_list(kb_safe)
    assert not any(c.startswith("exec:buy:") for c in cbs_safe)
    assert "menu" in cbs_safe

    bare_deps = types.SimpleNamespace()
    assert "exec:buy:ASII" in _cb_list(
        h_jurnal.confirm_exec_markup("rencana", "ASII", bare_deps, allow_buy=True)
    )
    assert "exec:buy:ASII" not in _cb_list(
        h_jurnal.confirm_exec_markup("rencana", "ASII", bare_deps, allow_buy=False)
    )


def test_jurnal_record_buy_dan_exec_done_markup_ada_alarm_dan_cek_jual(isolated_env):
    kb = h_jurnal.exec_done_markup("BBCA.JK", side="BELI")
    cbs = _cb_list(kb)
    for expected in ("manual:alert_add", "tick:jual:BBCA", "run:jurnal", "run:portofolio", "menu"):
        assert expected in cbs

    upd = _make_update(cid=9201, text="/jurnal beli BBCA 9000 2")
    asyncio.run(h_jurnal.do_jurnal(upd, ["beli", "BBCA", "9000", "2"], bot))
    assert upd.effective_message.replies
    txt, kw = upd.effective_message.replies[-1]
    assert "BELI BBCA" in txt
    cbs_reply = _cb_list(kw.get("reply_markup"))
    for expected in ("manual:alert_add", "tick:jual:BBCA", "run:jurnal", "run:portofolio", "menu"):
        assert expected in cbs_reply


def test_jurnal_riwayat_ada_ringkasan_portofolio_menu(isolated_env):
    cid = 9202
    upd_empty = _make_update(cid=cid, text="/jurnal riwayat")
    asyncio.run(h_jurnal.do_jurnal(upd_empty, ["riwayat"], bot))
    cbs_empty = _cb_list(upd_empty.effective_message.replies[-1][1].get("reply_markup"))
    for expected in ("run:jurnal", "run:portofolio", "menu"):
        assert expected in cbs_empty

    store.record_execution_buy(cid, "BBCA", 2, 9000.0, "2026-11-14")
    upd_full = _make_update(cid=cid, text="/jurnal riwayat")
    asyncio.run(h_jurnal.do_jurnal(upd_full, ["riwayat"], bot))
    cbs_full = _cb_list(upd_full.effective_message.replies[-1][1].get("reply_markup"))
    for expected in ("run:jurnal", "run:portofolio", "menu"):
        assert expected in cbs_full


def test_jurnal_exec_sell_saat_belum_pegang_beri_tombol_tindak_lanjut(isolated_env):
    cid = 9203
    q = _FakeQuery("exec:sell:BBCA", uid=cid, cid=cid)
    upd = _make_update(cid=cid, query=q)
    asyncio.run(h_jurnal.handle_exec_callback(upd, "exec:sell:BBCA", bot))
    assert cid not in guards.PENDING
    assert q.message.replies
    txt, kw = q.message.replies[-1]
    assert "belum punya posisi" in txt.lower()
    cbs = _cb_list(kw.get("reply_markup"))
    for expected in ("run:portofolio", "guide:jual", "exec:buy:BBCA", "menu"):
        assert expected in cbs


@pytest.mark.parametrize("bad_input", ["abc", "9000", "9000 0", "9000 abc", "0 2"])
def test_jurnal_pending_exec_error_format_selalu_ada_batal_dan_menu(isolated_env, bad_input):
    cid = 9204
    guards._pending_set(cid, "exec_buy:BBCA")
    upd = _make_update(cid=cid, text=bad_input)
    asyncio.run(h_jurnal.handle_pending_exec(upd, cid, "exec_buy:BBCA", bad_input, bot))
    assert upd.effective_message.replies
    _, kw = upd.effective_message.replies[-1]
    cbs = _cb_list(kw.get("reply_markup"))
    assert "exec:cancel" in cbs
    assert "menu" in cbs


def test_jurnal_record_sell_value_error_tetap_beri_markup(isolated_env):
    cid = 9205
    upd = _make_update(cid=cid, text="/jurnal jual BBCA 9500 2")
    asyncio.run(h_jurnal.do_jurnal(upd, ["jual", "BBCA", "9500", "2"], bot))
    assert upd.effective_message.replies
    _, kw = upd.effective_message.replies[-1]
    cbs = _cb_list(kw.get("reply_markup"))
    assert "run:portofolio" in cbs
    assert "menu" in cbs


# ---------------------------------------------------------------------------
# 3. handlers/analisis.py
# ---------------------------------------------------------------------------


def _patch_analisis_deps(
    monkeypatch,
    *,
    score_pct: float,
    rating: str,
    verdict: str = "",
    allow_buy_flag=None,
    ticker: str = "BBCA",
):
    ind = pd.DataFrame({"close": [8800.0, 9000.0], "atr14": [150.0, 150.0]})

    async def _fake_fetch(sym: str):
        out = {
            "ticker": ticker,
            "ind": ind,
            "snap": {"close": 9000.0, "ret20d": 4.0, "ret1d": 1.0, "atr14": 150.0},
            "score": {"pct": score_pct, "rating": rating},
            "sr": {"support": 8700.0, "resistance": 9400.0},
            "as_of": marketcal.now_wib().date().isoformat(),
        }
        if verdict:
            out["verdict"] = verdict
        if allow_buy_flag is not None:
            out["allow_buy"] = allow_buy_flag
        return out

    def _fake_chart(sym, _ind, _pct, path):
        with open(path, "wb") as f:
            f.write(b"PNG")

    monkeypatch.setattr(bot, "fetch_and_analyze", _fake_fetch)
    monkeypatch.setattr(
        bot, "build_report", lambda res, fsc, nsent, mkt_ctx: f"LAPORAN {res['ticker']}"
    )
    monkeypatch.setattr(
        bot, "build_quick", lambda res: f"SKOR {res['ticker']} {res['score']['pct']}"
    )
    monkeypatch.setattr(bot, "build_chart", _fake_chart)
    monkeypatch.setattr(banding, "market_context", lambda: None)
    monkeypatch.setattr(banding, "sector_context", lambda *a, **kw: None)
    monkeypatch.setattr(banding, "year_return", lambda s: 0.1)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(news, "fetch_news", lambda t: [])
    monkeypatch.setattr(data, "fetch_weekly", lambda t: None)


def test_do_analisis_positif_simpan_signal_context_dan_allow_buy(isolated_env, monkeypatch):
    _patch_analisis_deps(monkeypatch, score_pct=55, rating="STRONG BUY", ticker="BBCA.JK")
    cid = 9301
    upd = _make_update(cid=cid, text="/analisis BBCA")
    asyncio.run(h_analisis.do_analisis(upd, ["BBCA"], bot))

    ctx = store.get_signal_context(cid, "BBCA", intent="beli")
    assert ctx is not None
    assert ctx["source"] == "analisis"
    assert ctx["signal_price"] == pytest.approx(9000.0)
    assert ctx["sl"] is not None and ctx["tp"] is not None

    assert upd.effective_message.replies
    _, text_kw = upd.effective_message.replies[-1]
    text_cbs = _cb_list(text_kw.get("reply_markup"))
    assert "exec:buy:BBCA" in text_cbs
    assert "tick:fundamental:BBCA" in text_cbs
    assert "tick:rencana:BBCA" in text_cbs
    assert "refresh:analisis:BBCA" in text_cbs
    assert "menu" in text_cbs

    assert upd.effective_message.photos
    _, photo_kw = upd.effective_message.photos[-1]
    photo_cbs = _cb_list(photo_kw.get("reply_markup"))
    assert "exec:buy:BBCA" in photo_cbs


@pytest.mark.parametrize(
    ("pct", "rating", "verdict", "allow_flag"),
    [
        (-45, "SELL", "", None),
        (25, "BUY", "HINDARI", None),
        (40, "BUY", "", False),
    ],
)
def test_do_analisis_negatif_atau_allow_buy_false_tidak_simpan_context_dan_tanpa_buy(
    isolated_env, monkeypatch, pct, rating, verdict, allow_flag
):
    _patch_analisis_deps(
        monkeypatch,
        score_pct=pct,
        rating=rating,
        verdict=verdict,
        allow_buy_flag=allow_flag,
        ticker="ANTM",
    )
    cid = 9302
    upd = _make_update(cid=cid, text="/analisis ANTM")
    asyncio.run(h_analisis.do_analisis(upd, ["ANTM"], bot))

    assert store.get_signal_context(cid, "ANTM", intent="beli") is None

    _, text_kw = upd.effective_message.replies[-1]
    text_cbs = _cb_list(text_kw.get("reply_markup"))
    assert not any(c.startswith("exec:buy:") for c in text_cbs)
    assert "tick:jual:ANTM" in text_cbs
    assert "menu" in text_cbs

    _, photo_kw = upd.effective_message.photos[-1]
    photo_cbs = _cb_list(photo_kw.get("reply_markup"))
    assert not any(c.startswith("exec:buy:") for c in photo_cbs)


def test_do_skor_panggil_result_markup_dan_hormati_skor_negatif(isolated_env, monkeypatch):
    calls: list[tuple[str, str, bool]] = []
    orig_rm = bot.result_markup

    def _spy_rm(action: str, ticker: str, *, allow_buy: bool = True):
        calls.append((action, ticker, allow_buy))
        return orig_rm(action, ticker, allow_buy=allow_buy)

    monkeypatch.setattr(bot, "result_markup", _spy_rm)
    _patch_analisis_deps(monkeypatch, score_pct=45, rating="BUY", ticker="BBRI")
    upd_ok = _make_update(cid=9303, text="/skor BBRI")
    asyncio.run(h_analisis.do_skor(upd_ok, ["BBRI"], bot, from_menu=False))
    assert calls[-1] == ("skor", "BBRI", True)
    cbs_ok = _cb_list(upd_ok.effective_message.replies[-1][1].get("reply_markup"))
    assert "refresh:skor:BBRI" in cbs_ok
    assert "menu" in cbs_ok

    _patch_analisis_deps(monkeypatch, score_pct=-35, rating="SELL", ticker="BBRI")
    upd_neg = _make_update(cid=9303, text="/skor BBRI")
    asyncio.run(h_analisis.do_skor(upd_neg, ["BBRI"], bot, from_menu=False))
    assert calls[-1] == ("skor", "BBRI", False)
    cbs_neg = _cb_list(upd_neg.effective_message.replies[-1][1].get("reply_markup"))
    assert not any(c.startswith("exec:buy:") for c in cbs_neg)
    assert "refresh:skor:BBRI" in cbs_neg
    assert "menu" in cbs_neg

    # Guard TypeError untuk mock result_markup 2-arg lama: exec:buy tetap disaring saat skor negatif
    monkeypatch.setattr(
        bot,
        "result_markup",
        lambda action, t: InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Catat Beli {t}", callback_data=f"exec:buy:{t}")],
            [InlineKeyboardButton("Menu", callback_data="menu")],
        ]),
    )
    upd_legacy = _make_update(cid=9303, text="/skor BBRI")
    asyncio.run(h_analisis.do_skor(upd_legacy, ["BBRI"], bot, from_menu=False))
    cbs_legacy = _cb_list(upd_legacy.effective_message.replies[-1][1].get("reply_markup"))
    assert not any(c.startswith("exec:buy:") for c in cbs_legacy)
    assert "menu" in cbs_legacy


@pytest.mark.parametrize("bad_pct", [None, "N/A", float("nan")])
def test_do_skor_pct_none_atau_non_numerik_set_allow_buy_false(
    isolated_env, monkeypatch, bad_pct
):
    calls: list[tuple[str, str, bool]] = []
    orig_rm = bot.result_markup

    def _spy_rm(action: str, ticker: str, *, allow_buy: bool = True):
        calls.append((action, ticker, allow_buy))
        return orig_rm(action, ticker, allow_buy=allow_buy)

    monkeypatch.setattr(bot, "result_markup", _spy_rm)
    _patch_analisis_deps(monkeypatch, score_pct=bad_pct, rating="NEUTRAL", ticker="BBRI")
    upd = _make_update(cid=9305, text="/skor BBRI")
    asyncio.run(h_analisis.do_skor(upd, ["BBRI"], bot, from_menu=False))
    assert calls[-1] == ("skor", "BBRI", False)
    cbs = _cb_list(upd.effective_message.replies[-1][1].get("reply_markup"))
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert not any(c.startswith("tick:rencana:") for c in cbs)
    assert "tick:jual:BBRI" in cbs
    assert "menu" in cbs


def test_cmd_banding_sertakan_tombol_tindak_lanjut_emiten_utama(isolated_env, monkeypatch):
    monkeypatch.setattr(bot, "tokens", lambda upd: upd.message.text.split()[1:])

    async def _fake_fetch(sym: str):
        return {
            "ticker": sym,
            "ind": pd.DataFrame({"close": [8800.0, 9000.0]}),
            "snap": {"close": 9000.0, "ret5d": 1.5, "ret20d": 3.0},
            "score": {"pct": 35, "rating": "BUY"},
            "sr": {"support": 8700.0, "resistance": 9400.0},
            "as_of": "2026-11-14",
        }

    monkeypatch.setattr(bot, "fetch_and_analyze", _fake_fetch)
    monkeypatch.setattr(
        fundamental,
        "fetch_fundamentals",
        lambda t: {"per": 15.0, "pbv": 3.0, "roe": 20.0, "div_yield": 3.0, "currency": "IDR"},
    )
    monkeypatch.setattr(banding, "year_return", lambda s: 0.12)

    upd = _make_update(cid=9304, text="/banding BBCA.JK BBRI")
    asyncio.run(h_analisis.cmd_banding(upd, None, bot))
    assert len(upd.effective_message.replies) >= 2
    _, kw = upd.effective_message.replies[-1]
    cbs = _cb_list(kw.get("reply_markup"))
    assert "tick:cek:BBCA" in cbs
    assert "tick:analisis:BBCA" in cbs
    assert "tick:fundamental:BBCA" in cbs
    assert "menu" in cbs

    upd_1 = _make_update(cid=9304, text="/banding BBCA")
    asyncio.run(h_analisis.cmd_banding(upd_1, None, bot))
    assert "tick:cek:BBCA" in _cb_list(upd_1.effective_message.replies[-1][1].get("reply_markup"))

    async def _fail_fetch(sym: str):
        raise ValueError("offline")

    monkeypatch.setattr(bot, "fetch_and_analyze", _fail_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: None)
    upd_fail = _make_update(cid=9304, text="/banding XXXX YYYY")
    asyncio.run(h_analisis.cmd_banding(upd_fail, None, bot))
    assert "menu" in _cb_list(upd_fail.effective_message.replies[-1][1].get("reply_markup"))

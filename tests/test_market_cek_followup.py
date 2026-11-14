"""Tes offline alur sambung-menyambung & modal kecil di market, cek, dan trading."""
from __future__ import annotations

import asyncio
import math
import types

import numpy as np
import pytest

import bot
from core import (bandar, banding, cek as cekmod, data, fundamental, marketcal,
                  news, pricing, regime, rencana, saran as saranmod, screener,
                  store)
from handlers import cek as cek_handler
from handlers import market as market_handler
from handlers import trading as trading_handler
from tests.conftest import make_prices


class _FakeMsg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kw):
        self.replies.append((text, kw))
        return self

    async def edit_text(self, text: str, **kw):
        self.replies.append((text, kw))
        return self


class _FakeChat:
    id = 100

    async def send_action(self, *_a, **_kw):
        return None


class _FakeUser:
    id = 100


class _FakeUpdate:
    def __init__(self, text: str = ""):
        self.effective_message = _FakeMsg(text)
        self.effective_chat = _FakeChat()
        self.effective_user = _FakeUser()


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kw):
        self.sent.append((chat_id, text, kw))
        return types.SimpleNamespace(message_id=1)


def _ctx():
    return types.SimpleNamespace(job_queue=None, bot=_FakeBot(), args=[])


def _callbacks(markup) -> list[str]:
    if markup is None:
        return []
    out: list[str] = []
    for row in getattr(markup, "inline_keyboard", ()):
        for btn in row:
            cb = getattr(btn, "callback_data", None)
            if cb:
                assert len(cb.encode("utf-8")) <= 64
                out.append(cb)
    return out


def _last_callbacks(replies: list[tuple[str, dict]]) -> list[str]:
    assert replies
    return _callbacks(replies[-1][1].get("reply_markup"))


def _reset_guards():
    bot._last_used.clear()
    bot.PENDING.clear()
    bot.ALLOWED_IDS.clear()
    bot._screener_running = False


def _scan_row(
    ticker: str,
    *,
    sector: str = "Financials",
    close: float = 1000.0,
    atr: float = 30.0,
    value_avg20: float = 20_000_000_000.0,
    rel_pct: float = 90.0,
    score_pct: float = 55.0,
    signal: str = "BELI",
    board: str = "Utama",
) -> dict:
    return {
        "ticker": ticker,
        "name": f"PT {ticker} Tbk",
        "sector": sector,
        "board": board,
        "signal": signal,
        "price": close,
        "close": close,
        "atr": atr,
        "rsi": 54.0,
        "ret5d": 2.0,
        "ret20d": 6.0,
        "value_avg20": value_avg20,
        "rel_pct": rel_pct,
        "score_pct": score_pct,
        "tech_score": score_pct,
        "entry": close,
        "sl": close - atr,
        "tp": close + 2 * atr,
    }


def _cand(
    ticker: str = "BBCA",
    *,
    sector: str = "Financials",
    price: float = 1000.0,
    atr: float = 30.0,
    board: str = "Utama",
    value_avg20: float = 20_000_000_000.0,
    warnings: list[str] | None = None,
    vetoes: list[str] | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "name": f"PT {ticker} Tbk",
        "sector": sector,
        "board": board,
        "price": price,
        "atr": atr,
        "rel_pct": 92.0,
        "tech_score": 55.0,
        "grade": "A",
        "value_avg20": value_avg20,
        "warnings": list(warnings or []),
        "vetoes": list(vetoes or []),
        "score": {"pct": 55.0},
        "snap": {"close": price, "rsi": 54.0, "ret20d": 5.0},
        "ind": make_prices(np.linspace(price * 0.9, price, 80), volume=10_000_000),
    }


def _res_payload(
    ticker: str = "BBCA",
    *,
    close: float = 1000.0,
    atr: float = 30.0,
    score_pct: float = 40.0,
    rsi: float = 55.0,
    ret20d: float = 4.0,
    volume: float = 10_000_000.0,
    ind=None,
) -> dict:
    if ind is None:
        ind = make_prices(np.linspace(close * 0.9, close, 80), volume=volume)
        ind["atr14"] = atr
    return {
        "ticker": ticker,
        "snap": {"close": close, "rsi": rsi, "ret20d": ret20d},
        "score": {"pct": score_pct},
        "ind": ind,
        "stale_note": "",
        "as_of": "2026-11-14 16:00",
    }


def _mock_gather_and_enrich(
    monkeypatch, cand_by_ticker: dict[str, dict], *, mock_enrich: bool = False
):
    async def fake_gather(t):
        sym = str(t).upper()
        c = cand_by_ticker.get(sym)
        px = float(c["price"]) if c else 1000.0
        atr = float(c["atr"]) if c else 30.0
        ind = make_prices(np.linspace(px * 0.9, px, 80), volume=10_000_000)
        ind["atr14"] = atr
        return {
            "res": _res_payload(sym, close=px, atr=atr, score_pct=55.0, ind=ind),
            "fund_raw": {"per": 12.0, "pbv": 1.3, "roe": 16.0, "sector": c["sector"] if c else "Financials"},
            "fund": {"pct": 20.0, "label": "BAIK"},
            "nsent": {"pct": 15.0, "n": 1, "label": "POSITIF"},
        }

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    if mock_enrich:
        def fake_enrich(first, *_a, **_kw):
            if isinstance(first, list):
                return [
                    {k: v for k, v in cand_by_ticker[c["ticker"]].items() if k != "ind"}
                    for c in first
                    if c["ticker"] in cand_by_ticker
                ]
            sym = str(first.get("ticker") or "BBCA").upper()
            return {k: v for k, v in cand_by_ticker[sym].items() if k != "ind"}

        monkeypatch.setattr(rencana, "enrich", fake_enrich)


# --------------------------------------------------------------------- /paket
def test_paket_valid_picks_save_context_and_cap_four_pick_buttons(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 100_000_000)
    specs = (
        ("BBCA", "Financials"),
        ("TLKM", "Infrastructure"),
        ("ICBP", "Consumer Non-Cyclicals"),
        ("ASII", "Industrials"),
        ("ADRO", "Energy"),
        ("ANTM", "Basic Materials"),
    )
    rows = [
        _scan_row(t, sector=sec, close=1000.0, atr=60.0, rel_pct=95.0 - i)
        for i, (t, sec) in enumerate(specs)
    ]
    store.save_market_scan(rows, meta={"as_of": "2026-11-14 16:00"})

    async def fake_kval(ts):
        return {t: {"ok": True, "label": "sehat"} for t in ts}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    _mock_gather_and_enrich(
        monkeypatch,
        {t: _cand(t, sector=sec, price=1000.0, atr=60.0) for t, sec in specs},
    )
    monkeypatch.setattr(
        regime,
        "fetch_ihsg_regime",
        lambda *_a, **_kw: {
            "status": "BULL",
            "label": "konstruktif",
            "exposure_scale": 1.0,
        },
    )

    upd = _FakeUpdate("/paket 5")
    asyncio.run(bot.cmd_paket(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)

    cek_cbs = [c for c in cbs if c.startswith("tick:cek:")]
    rencana_cbs = [c for c in cbs if c.startswith("tick:rencana:")]
    buy_cbs = [c for c in cbs if c.startswith("exec:buy:")]
    assert len(cek_cbs) == 4
    assert len(rencana_cbs) == 4
    assert len(buy_cbs) == 4
    assert "sub:modal" in cbs
    assert "menu" in cbs

    ctx_bbca = store.get_signal_context(100, "BBCA", intent="beli")
    assert ctx_bbca is not None
    assert ctx_bbca["source"] == "paket"
    assert ctx_bbca["intent"] == "beli"
    assert ctx_bbca["signal_price"] > 0


def test_paket_no_snapshot_offers_scan_and_modal_markup(tmp_store):
    _reset_guards()
    upd = _FakeUpdate("/paket")
    asyncio.run(bot.cmd_paket(upd, _ctx()))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)
    assert "scan" in txt.lower()
    assert "run:scan" in cbs
    assert "sub:modal" in cbs
    assert "pick:rencana" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)


def test_paket_small_modal_no_fitting_pick_shows_followup_no_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 500_000)
    rows = [_scan_row("BBCA", close=10_000.0, atr=200.0)]
    store.save_market_scan(rows, meta={"as_of": "2026-11-14 16:00"})

    async def fake_kval(ts):
        return {t: {"ok": True, "label": "sehat"} for t in ts}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    _mock_gather_and_enrich(
        monkeypatch, {"BBCA": _cand("BBCA", price=10_000.0, atr=200.0)}
    )
    monkeypatch.setattr(
        regime,
        "fetch_ihsg_regime",
        lambda *_a, **_kw: {
            "status": "BULL",
            "label": "konstruktif",
            "exposure_scale": 1.0,
        },
    )

    upd = _FakeUpdate("/paket")
    asyncio.run(bot.cmd_paket(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)
    assert "run:sinyal_bagus" in cbs
    assert "sub:modal" in cbs
    assert "pick:rencana" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


# ------------------------------------------------------------------ /rencana
def test_rencana_no_args_renders_ticker_pick_markup(tmp_store):
    _reset_guards()
    store.watchlist_add(100, "BBCA")
    upd = _FakeUpdate("/rencana")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)
    assert "tick:rencana:BBCA" in cbs
    assert "manual:rencana" in cbs
    assert "menu" in cbs


def test_rencana_small_modal_explains_25pct_shows_full_modal_capacity_no_context(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 500_000)
    cand = _cand("BBCA", price=3000.0, atr=60.0)
    _mock_gather_and_enrich(monkeypatch, {"BBCA": cand})

    plan_direct = rencana.budget_plan(cand, 125_000, 500_000)
    txt_direct = market_handler._rencana_single_text(
        plan_direct, cand, budget_from_modal=True, deps=bot
    )
    assert "25%" in txt_direct
    assert "modal penuh" in txt_direct.lower()

    upd = _FakeUpdate("/rencana BBCA")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "25%" in txt
    assert "modal penuh" in txt.lower()
    assert "1 lot" in txt.lower()
    assert "manual:rencana" in cbs
    assert "sub:modal" in cbs
    assert "tick:cek:BBCA" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


def test_rencana_with_vetoes_does_not_save_context_or_allow_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 50_000_000)
    cand = _cand(
        "ILLQ",
        price=1000.0,
        atr=30.0,
        value_avg20=500_000_000.0,
        vetoes=["Likuiditas sangat tipis (< Rp2 M/hari)"],
    )
    _mock_gather_and_enrich(monkeypatch, {"ILLQ": cand}, mock_enrich=True)

    upd = _FakeUpdate("/rencana ILLQ")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "tick:analisis:ILLQ" in cbs
    assert "tick:jual:ILLQ" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "ILLQ", intent="beli") is None


def test_rencana_valid_plan_explains_25pct_saves_context_and_shows_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 20_000_000)
    cand = _cand("BBCA", price=1000.0, atr=30.0)
    _mock_gather_and_enrich(monkeypatch, {"BBCA": cand})

    upd = _FakeUpdate("/rencana BBCA")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "25%" in txt
    assert "exec:buy:BBCA" in cbs
    ctx_saved = store.get_signal_context(100, "BBCA", intent="beli")
    assert ctx_saved is not None
    assert ctx_saved["source"] == "rencana"


def test_rencana_limit_reject_uses_limit_markup_and_no_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 10_000_000)
    store.add_real_position(100, "BBCA", 30, 1000.0, "2026-11-10", sector="Financials")
    cand = _cand("BBCA", price=1000.0, atr=30.0)
    _mock_gather_and_enrich(monkeypatch, {"BBCA": cand})

    upd = _FakeUpdate("/rencana BBCA")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)
    assert "tick:cek:BBCA" in cbs
    assert "run:portofolio" in cbs
    assert "sub:modal" in cbs
    assert "run:sinyal_bagus" in cbs
    assert "manual:rencana" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


def test_rencana_small_modal_500rb_limit_reject_gives_beginner_cta_no_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 500_000)
    cand = _cand("LPIN", price=2000.0, atr=40.0)
    _mock_gather_and_enrich(monkeypatch, {"LPIN": cand})

    kb_direct = market_handler._rencana_limit_reject_markup("LPIN")
    cbs_direct = [btn.callback_data for row in kb_direct.inline_keyboard for btn in row]
    assert "tick:cek:LPIN" in cbs_direct
    assert "run:portofolio" in cbs_direct
    assert "sub:modal" in cbs_direct
    assert "run:sinyal_bagus" in cbs_direct
    assert "menu" in cbs_direct
    assert not any(c.startswith("exec:buy:") for c in cbs_direct)

    upd = _FakeUpdate("/rencana LPIN 500000")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "/portofolio batas" in txt
    assert "1 lot lebih murah" in txt.lower()
    assert "/rencana LPIN 3000000" not in txt
    assert "tick:cek:LPIN" in cbs
    assert "run:portofolio" in cbs
    assert "sub:modal" in cbs
    assert "run:sinyal_bagus" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "LPIN", intent="beli") is None


# ------------------------------------------------------------ /scan & /bandar
def test_scan_cache_hit_and_job_success_attach_followup_markup(
    monkeypatch, tmp_store
):
    _reset_guards()
    today_iso = marketcal.now_wib().date().isoformat()
    store.save_market_scan(
        [_scan_row("BBCA")],
        meta={"as_of": "2026-11-14 16:00"},
    )
    monkeypatch.setattr(
        store,
        "market_scan_meta",
        lambda: {"scanned_at": f"{today_iso}T09:15:00+07:00", "as_of": "2026-11-14 16:00"},
    )

    async def fake_kval(tickers):
        return {t: {"ok": True, "label": "sehat"} for t in tickers}

    monkeypatch.setattr(bot, "_kualitas_map", fake_kval)
    monkeypatch.setattr(
        screener,
        "scan",
        lambda force=False: {"ok": 10, "beli": 2, "jual": 1, "total": 10},
    )

    upd = _FakeUpdate("/scan")
    asyncio.run(bot.cmd_scan(upd, _ctx()))
    cbs_cache = _last_callbacks(upd.effective_message.replies)
    for expected in ("sg:good", "sg:ov", "guide:beli", "menu"):
        assert expected in cbs_cache

    bot._screener_running = True
    fake_ctx = types.SimpleNamespace(
        bot=_FakeBot(), job=types.SimpleNamespace(data={"chat_id": 100})
    )
    asyncio.run(bot._run_scan_job(fake_ctx))
    assert fake_ctx.bot.sent
    cbs_job = _callbacks(fake_ctx.bot.sent[-1][2].get("reply_markup"))
    for expected in ("sg:good", "sg:ov", "guide:beli", "menu"):
        assert expected in cbs_job


def test_bandar_with_and_without_ticker_returns_followup_buttons(monkeypatch):
    _reset_guards()
    monkeypatch.setattr(bandar, "last_trading_date", lambda: "20261114")
    monkeypatch.setattr(
        bandar,
        "get_index_summary",
        lambda: [{"IndexCode": "COMPOSITE", "Date": "2026-11-14", "IndexClose": 7300.0}],
    )
    monkeypatch.setattr(
        bandar,
        "get_stock_summary",
        lambda *_a, **_kw: [
            {
                "StockCode": "BBCA",
                "StockName": "Bank Central Asia",
                "OpenPrice": 9900,
                "HighestPrice": 10100,
                "LowestPrice": 9850,
                "ClosingPrice": 10000,
                "Change": 100,
                "Previous": 9900,
                "Volume": 50_000_000,
                "Value": 500_000_000_000,
                "ForeignBuy": 200_000_000_000,
                "ForeignSell": 150_000_000_000,
            },
            {
                "StockCode": "BBRI",
                "StockName": "Bank Rakyat Indonesia",
                "ClosingPrice": 4500,
                "Change": 200,
                "Previous": 4300,
                "Volume": 90_000_000,
            },
        ],
    )

    upd_t = _FakeUpdate("/bandar BBCA")
    asyncio.run(bot.cmd_bandar(upd_t, _ctx()))
    cbs_t = _last_callbacks(upd_t.effective_message.replies)
    assert "tick:cek:BBCA" in cbs_t
    assert "tick:analisis:BBCA" in cbs_t
    assert "run:sektor" in cbs_t
    assert "menu" in cbs_t

    _reset_guards()
    upd_all = _FakeUpdate("/bandar")
    asyncio.run(bot.cmd_bandar(upd_all, _ctx()))
    cbs_all = _last_callbacks(upd_all.effective_message.replies)
    assert any(c.startswith("tick:cek:") for c in cbs_all)
    assert any(c.startswith("tick:analisis:") for c in cbs_all)
    assert "run:sektor" in cbs_all
    assert "menu" in cbs_all


# ----------------------------------------------------------------------- /cek
def _mock_cek_deps(
    monkeypatch,
    *,
    score_pct: float = 40.0,
    rsi: float = 55.0,
    ret20d: float = 4.0,
    fund_raw: dict | None = None,
    fund_score: dict | None = None,
    nsent: dict | None = None,
    bandar_row: dict | None = None,
    volume: float = 10_000_000.0,
):
    res = _res_payload(
        "BBCA",
        close=1000.0,
        atr=30.0,
        score_pct=score_pct,
        rsi=rsi,
        ret20d=ret20d,
        volume=volume,
    )

    async def fake_fetch(t):
        return dict(res, ticker=t)

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda _t: fund_raw)
    monkeypatch.setattr(fundamental, "score_fundamentals", lambda _r: fund_score)
    monkeypatch.setattr(
        news,
        "fetch_news",
        lambda _t: [{"title": "Laba stabil"}] if nsent is not None else [],
    )
    monkeypatch.setattr(news, "news_sentiment", lambda _items: nsent)
    monkeypatch.setattr(
        data,
        "fetch_weekly",
        lambda _t: make_prices(np.linspace(900, 1000, 60)),
    )
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda _t: bandar_row)
    monkeypatch.setattr(bot, "_cek_agenda", lambda _t: (None, None))
    monkeypatch.setattr(
        banding,
        "sector_context",
        lambda *_a, **_kw: {"ret20d": 1.0, "sector": "Financials"},
    )
    return res


def test_cek_text_uses_user_modal_and_1_lot_without_hardcoded_5m(tmp_store):
    out = {
        "ticker": "BBCA",
        "price": 1000.0,
        "sector": "Financials",
        "board": "Utama",
        "items": [],
        "verdict": "LAYAK",
        "reasons": ["Tren positif"],
        "warnings": [],
        "missing": [],
    }
    res = _res_payload("BBCA", close=1000.0)
    txt = cek_handler._cek_text(out, res, bot, modal=500_000, max_pos_pct=0.25)
    assert "5000000" not in txt
    assert "500.000" in txt
    assert "Biaya 1 lot" in txt
    assert "/rencana BBCA 500000" in txt

    # Kompatibilitas pemanggilan 3-argumen lama tetap aman.
    txt_compat = bot._cek_text(out, res)
    assert "5000000" not in txt_compat
    assert "Biaya 1 lot" in txt_compat


def test_cek_slash_layak_saves_context_and_has_nav_buttons(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 10_000_000)
    _mock_cek_deps(
        monkeypatch,
        score_pct=45.0,
        fund_raw={"per": 12.0, "pbv": 1.4, "roe": 18.0, "sector": "Financials"},
        fund_score={"pct": 20.0, "label": "BAIK"},
        nsent={
            "pct": 20.0,
            "n": 1,
            "label": "POSITIF",
            "details": [{"title": "Laba naik"}],
        },
        bandar_row={"ForeignNet": 4_000_000_000},
    )

    upd = _FakeUpdate("/cek BBCA")
    asyncio.run(bot.cmd_cek(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "tick:fundamental:BBCA" in cbs
    assert "tick:rencana:BBCA" in cbs
    assert "exec:buy:BBCA" in cbs
    assert "menu" in cbs
    ctx_saved = store.get_signal_context(100, "BBCA", intent="beli")
    assert ctx_saved is not None
    assert ctx_saved["source"] == "cek"


@pytest.mark.parametrize("case", ["HINDARI", "TIDAK_CUKUP_DATA"])
def test_cek_hindari_or_insufficient_hides_buy_and_skips_context(
    case, monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 10_000_000)
    if case == "HINDARI":
        _mock_cek_deps(
            monkeypatch,
            score_pct=-60.0,
            rsi=76.0,
            ret20d=-12.0,
            fund_raw={"per": 80.0, "pbv": 9.0, "roe": -5.0, "sector": "Consumer"},
            fund_score={"pct": -30.0, "label": "BURUK"},
            nsent={
                "pct": -35.0,
                "n": 2,
                "label": "NEGATIF",
                "details": [{"title": "Rugi meningkat"}],
            },
            bandar_row={"ForeignNet": -8_000_000_000},
            volume=100.0,
        )
    else:
        async def sparse_fetch(t):
            return {
                "ticker": t,
                "snap": {"close": 1000.0},
                "score": {"pct": 0.0},
                "ind": make_prices(np.full(5, 1000.0), volume=0.0),
                "stale_note": "",
            }

        _mock_cek_deps(
            monkeypatch,
            fund_raw=None,
            fund_score=None,
            nsent=None,
            bandar_row=None,
            volume=0.0,
        )
        monkeypatch.setattr(bot, "fetch_and_analyze", sparse_fetch)
        monkeypatch.setattr(cekmod, "build", lambda **_kw: {
            "ticker": "BBCA",
            "price": 1000.0,
            "sector": None,
            "board": "Utama",
            "items": [],
            "verdict": "TIDAK CUKUP DATA",
            "reasons": ["Data belum cukup"],
            "warnings": [],
            "missing": ["fundamental", "berita"],
        })

    upd = _FakeUpdate("/cek BBCA")
    asyncio.run(bot.cmd_cek(upd, _ctx()))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "disembunyikan" in txt.lower()
    assert "tick:fundamental:BBCA" in cbs
    assert "tick:analisis:BBCA" in cbs
    assert "tick:jual:BBCA" in cbs
    assert "menu" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert not any(c.startswith("tick:rencana:") for c in cbs)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


# ---------------------------------------------------------- /saran & do_tanya
def test_saran_list_only_returns_scan_modal_and_menu_markup(tmp_store):
    _reset_guards()
    upd = _FakeUpdate("/saran")
    asyncio.run(trading_handler.do_saran(upd, bot, list_only=True, from_menu=True))
    cbs = _last_callbacks(upd.effective_message.replies)
    assert "run:saran" in cbs
    assert "sub:modal" in cbs
    assert "menu" in cbs


@pytest.mark.parametrize(
    "reg_name,eff_risk_frac",
    [("BULL", saranmod.BASE_RISK_PCT), ("DEFENSIVE", saranmod.BASE_RISK_PCT * 0.5)],
)
def test_saran_min_modal_formula_zero_lots_and_no_buy_button(
    reg_name, eff_risk_frac, monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 500_000)

    async def fake_fetch(t):
        return _res_payload(t, close=10_000.0, atr=300.0, score_pct=65.0)

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(
        regime,
        "fetch_ihsg_regime",
        lambda *_a, **_kw: {
            "status": reg_name,
            "label": reg_name.lower(),
            "exposure_scale": 0.5 if reg_name == "DEFENSIVE" else 1.0,
        },
    )
    monkeypatch.setattr(
        fundamental,
        "fetch_fundamentals",
        lambda _t: {"per": 10.0, "pbv": 1.2, "roe": 18.0, "sector": "Financials"},
    )
    monkeypatch.setattr(
        fundamental,
        "score_fundamentals",
        lambda _f: {"pct": 25.0, "label": "BAIK"},
    )
    monkeypatch.setattr(news, "fetch_news", lambda _t: [{"title": "Positif"}])
    monkeypatch.setattr(
        news,
        "news_sentiment",
        lambda _i: {"pct": 25.0, "n": 1, "label": "POSITIF"},
    )
    monkeypatch.setattr(saranmod, "DEFAULT_UNIVERSE", ["BBCA"])

    upd = _FakeUpdate("/saran")
    asyncio.run(trading_handler.do_saran(upd, bot, from_menu=True))
    txt, _ = upd.effective_message.replies[-1]
    cbs = _last_callbacks(upd.effective_message.replies)

    entry_px = 10_000.0
    sl_px = float(saranmod.stop_price(entry_px, 300.0))
    real_risk_1_lot = max(entry_px - sl_px, 0.0) * 100.0
    expected_min_modal = math.ceil(max(real_risk_1_lot / eff_risk_frac, entry_px * 100.0))
    assert saranmod.position_size(expected_min_modal, entry_px, sl_px, risk_pct=eff_risk_frac)["lots"] >= 1
    assert f"Rp{expected_min_modal:,.0f}".replace(",", ".") in txt
    assert "tick:cek:BBCA" in cbs
    assert "tick:rencana:BBCA" in cbs
    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert "sub:modal" in cbs
    assert "run:sinyal_bagus" in cbs
    assert "run:portofolio" in cbs
    assert "menu" in cbs
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


def test_saran_valid_pick_and_exit_alert_save_context_and_buttons(
    monkeypatch, tmp_store
):
    _reset_guards()
    store.set_modal(100, 50_000_000)
    store.add_real_position(100, "TLKM", 10, 3000.0, "2026-11-10", sector="Infrastructure")

    async def fake_fetch(t):
        if t == "TLKM":
            return _res_payload("TLKM", close=2500.0, atr=80.0, score_pct=-35.0)
        return _res_payload(t, close=1000.0, atr=30.0, score_pct=70.0)

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(
        regime,
        "fetch_ihsg_regime",
        lambda *_a, **_kw: {
            "status": "BULL",
            "label": "konstruktif",
            "exposure_scale": 1.0,
        },
    )
    monkeypatch.setattr(
        fundamental,
        "fetch_fundamentals",
        lambda _t: {"per": 10.0, "pbv": 1.2, "roe": 18.0, "sector": "Financials"},
    )
    monkeypatch.setattr(
        fundamental,
        "score_fundamentals",
        lambda _f: {"pct": 25.0, "label": "BAIK"},
    )
    monkeypatch.setattr(news, "fetch_news", lambda _t: [{"title": "Positif"}])
    monkeypatch.setattr(
        news,
        "news_sentiment",
        lambda _i: {"pct": 25.0, "n": 1, "label": "POSITIF"},
    )
    monkeypatch.setattr(saranmod, "DEFAULT_UNIVERSE", ["BBCA"])

    upd = _FakeUpdate("/saran")
    asyncio.run(trading_handler.do_saran(upd, bot, from_menu=True))
    cbs = _last_callbacks(upd.effective_message.replies)

    assert "tick:cek:BBCA" in cbs
    assert "tick:rencana:BBCA" in cbs
    assert "exec:buy:BBCA" in cbs
    assert "tick:jual:TLKM" in cbs
    assert "exec:sell:TLKM" in cbs
    assert "sub:modal" in cbs
    assert "run:sinyal_bagus" in cbs
    assert "run:portofolio" in cbs
    assert "menu" in cbs

    buy_ctx = store.get_signal_context(100, "BBCA", intent="beli")
    assert buy_ctx is not None
    assert buy_ctx["source"] == "saran"
    sell_ctx = store.get_signal_context(100, "TLKM", intent="jual")
    assert sell_ctx is not None
    assert sell_ctx["source"] == "saran"


def _mock_tanya_deps(monkeypatch):
    async def fake_fetch(t):
        return _res_payload(t, close=1000.0, atr=30.0, score_pct=30.0)

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(
        fundamental,
        "fetch_fundamentals",
        lambda _t: {"per": 12.0, "pbv": 1.3, "roe": 16.0, "sector": "Financials"},
    )
    monkeypatch.setattr(
        fundamental,
        "score_fundamentals",
        lambda _f: {"pct": 20.0, "label": "BAIK"},
    )
    monkeypatch.setattr(news, "fetch_news", lambda _t: [{"title": "Stabil"}])
    monkeypatch.setattr(
        news,
        "news_sentiment",
        lambda _i: {"pct": 15.0, "n": 1, "label": "POSITIF"},
    )
    monkeypatch.setattr(
        data,
        "fetch_weekly",
        lambda _t: make_prices(np.linspace(900, 1000, 60)),
    )


def test_tanya_beli_hindari_hides_exec_buy_and_skips_signal_context(
    monkeypatch, tmp_store
):
    _reset_guards()
    _mock_tanya_deps(monkeypatch)
    monkeypatch.setattr(
        bot,
        "build_verdict",
        lambda *_a, **_kw: "🔴 <b>HINDARI / JANGAN BELI BBCA</b>\nTren turun tajam.",
    )

    upd = _FakeUpdate("/beli BBCA")
    asyncio.run(bot.cmd_beli(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)

    assert not any(c.startswith("exec:buy:") for c in cbs)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None


def test_tanya_free_text_and_positive_beli_respect_verdict(
    monkeypatch, tmp_store
):
    _reset_guards()
    _mock_tanya_deps(monkeypatch)

    monkeypatch.setattr(
        bot,
        "build_verdict",
        lambda *_a, **_kw: "🔴 <b>HINDARI BBCA</b>\nRisiko tinggi.",
    )
    upd_free = _FakeUpdate("BBCA beli gak?")
    asyncio.run(trading_handler.do_tanya(upd_free, bot, "BBCA", "umum"))
    cbs_free = _last_callbacks(upd_free.effective_message.replies)
    assert not any(c.startswith("exec:buy:") for c in cbs_free)
    assert store.get_signal_context(100, "BBCA", intent="beli") is None

    monkeypatch.setattr(
        bot,
        "build_verdict",
        lambda *_a, **_kw: "🟢 <b>LAYAK BELI BBCA</b>\nTren sehat & skor tinggi.",
    )
    upd_ok = _FakeUpdate("/beli BBCA")
    asyncio.run(bot.cmd_beli(upd_ok, _ctx()))
    cbs_ok = _last_callbacks(upd_ok.effective_message.replies)
    assert "exec:buy:BBCA" in cbs_ok
    ctx_saved = store.get_signal_context(100, "BBCA", intent="beli")
    assert ctx_saved is not None
    assert ctx_saved["source"] == "tanya_beli"


def test_tanya_jual_preserves_jual_context_and_hides_exec_buy(
    monkeypatch, tmp_store
):
    _reset_guards()
    _mock_tanya_deps(monkeypatch)
    monkeypatch.setattr(
        bot,
        "build_verdict",
        lambda *_a, **_kw: "🟡 <b>TAHAN / PERTIMBANGKAN JUAL BBCA</b>",
    )

    upd = _FakeUpdate("/jual BBCA")
    asyncio.run(bot.cmd_jual(upd, _ctx()))
    cbs = _last_callbacks(upd.effective_message.replies)

    assert not any(c.startswith("exec:buy:") for c in cbs)
    ctx_jual = store.get_signal_context(100, "BBCA", intent="jual")
    assert ctx_jual is not None
    assert ctx_jual["source"] == "tanya_jual"
    assert pricing.round_tick(1000.0) == pytest.approx(ctx_jual["signal_price"])

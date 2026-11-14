"""Tes offline forensic arus kas, jebakan dividen & debat Pro/Risiko (/cek)."""
from __future__ import annotations

import asyncio
import types

import numpy as np
import yfinance as yf

import bot
from core import banding, cek, data, fundamental, news, rencana, store
from tests.conftest import make_prices


def test_fetch_fundamentals_mengambil_field_arus_kas(monkeypatch):
    class _FakeTicker:
        info = {
            "longName": "PT Kas Kuat Tbk",
            "sector": "Consumer Defensive",
            "trailingPE": 12.0,
            "priceToBook": 2.0,
            "returnOnEquity": 0.20,
            "dividendYield": 0.04,
            "operatingCashflow": 3_000_000_000_000,
            "freeCashflow": 2_000_000_000_000,
            "netIncomeToCommon": 2_200_000_000_000,
            "totalCash": 5_000_000_000_000,
            "totalDebt": 1_000_000_000_000,
            "currentRatio": 2.4,
            "payoutRatio": 0.45,
        }
        quarterly_income_stmt = None

    fundamental.clear_cache()
    monkeypatch.setattr(yf, "Ticker", lambda s: _FakeTicker())
    got = fundamental.fetch_fundamentals("UNVR")
    fundamental.clear_cache()
    assert got is not None
    assert got["ocf"] == 3_000_000_000_000
    assert got["fcf"] == 2_000_000_000_000
    assert got["current_ratio"] == 2.4
    assert got["payout_ratio"] == 0.45


def test_forensic_akrual_dan_dividend_trap_non_bank():
    bad = {
        "sector": "Industrials",
        "per": 8.0,
        "pbv": 1.0,
        "roe": 0.16,
        "div_yield": 0.08,
        "ocf": -500e9,
        "net_income": 1_000e9,
        "fcf": -800e9,
        "total_cash": 200e9,
        "total_debt": 900e9,
        "current_ratio": 0.85,
        "payout_ratio": 1.30,
    }
    cf = fundamental.analyze_cashflow_quality(bad)
    assert cf["available"] is True
    assert cf["fin_sector"] is False
    flags_joined = " | ".join(cf["flags"]).lower()
    assert "akrual" in flags_joined
    assert "free cash flow (fcf) negatif" in flags_joined
    assert "current ratio" in flags_joined
    assert "130%" in flags_joined
    assert "utang lebih dari 2" in flags_joined

    sc = fundamental.score_fundamentals(bad)
    assert any("⚠️" in n for n in sc["notes"])
    assert sc["forensic_penalty"] > 0
    assert sc["forensic_flags"]
    assert sc["pct"] < sc["base_pct"]
    assert sc["label"] != "FUNDAMENTAL KUAT"
    assert "POJK 14" in sc["filing_note"]


def test_forensic_yield_tinggi_fcf_negatif_ditandai_dividend_trap():
    cf = fundamental.analyze_cashflow_quality({
        "sector": "Energy",
        "div_yield": 0.075,
        "fcf": -300e9,
        "payout_ratio": 0.60,
    })
    assert any("dividend trap" in f.lower() for f in cf["flags"])


def test_forensic_bank_tidak_dihukum_aturan_manufaktur():
    bank = {
        "sector": "Financial Services",
        "ocf": -20_000e9,
        "net_income": 10_000e9,
        "fcf": -22_000e9,
        "total_cash": 100_000e9,
        "total_debt": 500_000e9,
        "current_ratio": 0.40,
        "payout_ratio": 0.45,
    }
    cf = fundamental.analyze_cashflow_quality(bank)
    assert cf["available"] is True
    assert cf["fin_sector"] is True
    assert cf["flags"] == []


def test_forensic_data_kosong_jujur_tidak_mengarang():
    cf = fundamental.analyze_cashflow_quality({"per": 12.0, "pbv": 1.5, "roe": 0.15})
    assert cf["available"] is False
    assert cf["flags"] == []
    assert cf["strengths"] == []


def test_cek_dan_rencana_memunculkan_pro_risiko_dan_peringatan_kas():
    res = {
        "ticker": "ASII",
        "snap": {"close": 5000.0},
        "score": {"pct": 40.0},
        "weekly": {"direction": "naik", "pct_vs_ma": 2.5},
    }
    fund_raw = {
        "per": 10.0,
        "pbv": 1.3,
        "roe": 16.0,
        "sector": "Industrials",
        "ocf": -200e9,
        "net_income": 800e9,
        "fcf": -300e9,
        "payout_ratio": 1.15,
    }
    out = cek.build(
        res=res,
        fund={"pct": 25.0},
        fund_raw=fund_raw,
        nsent={"pct": 25.0, "n": 4, "top": "Kontrak baru"},
        bandar_row={"ForeignNet": 2_000_000_000},
        agenda=None,
        agenda_days=None,
        peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20_000_000_000.0,
        board="Utama",
    )
    assert out["bull_points"]
    assert out["bear_points"]
    assert any("akrual" in w.lower() for w in out["warnings"])
    assert any("akrual" in b.lower() or "115%" in b for b in out["bear_points"])

    ind = make_prices(np.linspace(4500, 5000, 80), volume=10_000_000)
    ind["atr14"] = 120.0
    enriched = rencana.enrich(
        {"ticker": "ASII", "rel_pct": 75.0, "sector": "Industrials"},
        {
            "res": {"ticker": "ASII", "snap": {"close": 5000.0, "rsi": 55.0, "ret20d": 4.0}, "ind": ind},
            "fund": {"pct": 25.0},
            "fund_raw": fund_raw,
            "nsent": {"pct": 10.0},
        },
        require_rel=False,
    )
    assert enriched is not None
    assert any("akrual" in w.lower() for w in enriched["warnings"])


def test_handler_cek_dan_fundamental_menampilkan_bagian_baru(monkeypatch, tmp_store):
    class _Msg:
        def __init__(self, text=""):
            self.text = text
            self.replies = []

        async def reply_text(self, text, **kw):
            self.replies.append((text, kw))
            return self

    class _Chat:
        id = 777

        async def send_action(self, *a, **kw):
            return None

    class _User:
        id = 777

    class _Upd:
        def __init__(self, text):
            self.effective_message = _Msg(text)
            self.effective_chat = _Chat()
            self.effective_user = _User()

    ind = make_prices(np.linspace(4500, 5000, 80), volume=10_000_000)
    ind["atr14"] = 120.0

    async def fake_fetch(t):
        return {
            "ticker": t,
            "snap": {"close": 5000.0, "rsi": 55.0, "ret20d": 3.0},
            "score": {"pct": 35.0},
            "ind": ind,
            "stale_note": "",
        }

    rich_fund = {
        "name": "PT Kas Bagus Tbk",
        "sector": "Energy",
        "per": 9.0,
        "pbv": 1.2,
        "roe": 0.19,
        "div_yield": 0.04,
        "debt_eq": 25.0,
        "market_cap": 50e12,
        "ocf": 6e12,
        "fcf": 4e12,
        "net_income": 4.5e12,
        "total_cash": 8e12,
        "total_debt": 2e12,
        "current_ratio": 2.1,
        "payout_ratio": 0.45,
        "as_of": "Q3 2026",
        "rev_trend": [("Q2 2026", 10e12), ("Q3 2026", 11e12)],
        "profit_trend": [("Q2 2026", 1e12), ("Q3 2026", 1.2e12)],
    }

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: rich_fund)
    monkeypatch.setattr(news, "fetch_news", lambda t: [{"title": "Laba naik"}])
    monkeypatch.setattr(news, "news_sentiment", lambda items: {"pct": 25.0, "n": 1})
    monkeypatch.setattr(data, "fetch_weekly", lambda t: make_prices(np.linspace(4500, 5000, 60)))
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda t: {"ForeignNet": 5_000_000_000})
    monkeypatch.setattr(bot, "_cek_agenda", lambda t: (None, None))
    monkeypatch.setattr(banding, "sector_context", lambda *a, **k: {"ret20d": 1.0, "sector": "Energy"})
    monkeypatch.setattr(store, "get_watchlist", lambda cid: [])
    bot._last_used.clear()

    upd_cek = _Upd("/cek PTBA")
    asyncio.run(bot.cmd_cek(upd_cek, types.SimpleNamespace()))
    cek_txt = "\n".join(t for t, _ in upd_cek.effective_message.replies)
    assert "Sudut Pandang Pro vs Risiko" in cek_txt
    assert "🟢" in cek_txt

    upd_fund = _Upd("/fundamental PTBA")
    asyncio.run(bot.cmd_fundamental(upd_fund, types.SimpleNamespace()))
    fund_txt = "\n".join(t for t, _ in upd_fund.effective_message.replies)
    assert "OCF" in fund_txt and "FCF" in fund_txt
    assert "Kas bersih" in fund_txt and "CR 2.10" in fund_txt
    assert "Payout 45%" in fund_txt

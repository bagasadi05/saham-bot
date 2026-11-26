"""Tes offline menyeluruh untuk kontrak real mesin desktop & utilitas `desktop.py`."""
from __future__ import annotations

import csv
from types import SimpleNamespace

from PIL import Image

from core import bandar
from core import chart as chartmod
from core import data as datamod
from core import desktop_helpers as dh
from core import desktop_monitor as dm
from core import fundamental
from core import news as newsmod
from core import store_portfolio
from core import store_profile
from core import store_scan


def _sample_fund(sector: str = "Communication Services", currency: str = "IDR") -> dict:
    return {
        "symbol": "TLKM",
        "sector": sector,
        "currency": currency,
        "per": 11.5,
        "pbv": 1.6,
        "roe": 18.0,
        "der": 0.45,
        "div_yield": 4.2,
        "ev_ebitda": 5.8,
        "fcf": 12_000_000_000_000.0,
        "market_cap": 300_000_000_000_000.0,
        "cfo": 22_000_000_000_000.0,
        "net_income": 15_000_000_000_000.0,
        "capex": -8_000_000_000_000.0,
        "debt_total": 35_000_000_000_000.0,
        "cash": 25_000_000_000_000.0,
        "revenue": 110_000_000_000_000.0,
        "filing_date": "2026-10-30",
    }


def test_real_fundamental_plural_wrappers_and_singular_fallback(monkeypatch):
    monkeypatch.setattr(fundamental, "_fetch_uncached", lambda sym: _sample_fund())
    raw = dm._fetch_fund_raw(fundamental, "TLKM")
    assert raw is not None and raw["per"] == 11.5

    scored = dm._score_fund_dict(fundamental, raw)
    assert scored is not None
    assert "pct" in scored and isinstance(scored["pct"], (int, float))
    assert "label" in scored

    fake_singular = SimpleNamespace(
        fetch_fundamental=lambda s: {"symbol": s, "per": 9.0},
        score_fundamental=lambda f: {"pct": 42, "label": "KUAT", "raw": f},
    )
    assert dm._fetch_fund_raw(fake_singular, "ASII")["per"] == 9.0
    assert dm._score_fund_dict(fake_singular, {"per": 9.0})["pct"] == 42


def test_inspect_ticker_detail_real_plan_syariah_and_chart_build(
    monkeypatch, tmp_store, tmp_path, uptrend_df,
):
    monkeypatch.setattr(
        datamod,
        "fetch_history",
        lambda sym, period="1y", raw_symbol=False: uptrend_df.copy(),
    )
    monkeypatch.setattr(datamod, "fetch_quote_name", lambda sym: "Telkom Indonesia")
    monkeypatch.setattr(fundamental, "_fetch_uncached", lambda sym: _sample_fund())
    monkeypatch.setattr(
        newsmod,
        "fetch_news",
        lambda sym, limit=8: [
            {"title": "TLKM ekspansi infrastruktur data center tumbuh solid", "source": "Bisnis", "published": ""}
        ],
    )
    monkeypatch.setattr(bandar, "call_with_stale", lambda *a, **kw: ([], None))

    detail = dm.inspect_ticker_detail("TLKM", 20_000_000, chat_id=0, period="6mo")
    assert detail["ok"] is True
    assert detail["period"] == "6mo"
    assert detail["syariah"] is True
    assert detail["plan"]["ok"] is True
    assert detail["plan"]["lots"] > 0
    assert detail["plan"]["buy_cost"] > 0
    assert detail["scale_out"]["tp1"] > detail["plan"]["entry"]
    assert detail["powerscore"]["pct"] is not None
    assert detail["cek"]["verdict"] in ("LAYAK", "TUNGGU", "HINDARI")

    out_png = tmp_path / "tlkm_chart.png"
    res = detail["res"]
    built = chartmod.build_chart(
        "TLKM",
        res["ind"],
        float(res["score"]["pct"]),
        str(out_png),
        levels={
            "entry": detail["plan"]["entry"],
            "sl": detail["plan"]["sl"],
            "tp": detail["plan"]["tp"],
        },
    )
    assert built == str(out_png)
    assert out_png.is_file() and out_png.stat().st_size > 1024
    with Image.open(out_png) as img:
        assert img.width > 400 and img.height > 300


def test_load_sinyal_rows_filters_search_sector_syariah_and_quality(monkeypatch, tmp_store):
    rows = [
        {
            "ticker": "TLKM", "name": "Telkom Indonesia", "board": "Utama",
            "trade_date": "2026-11-24", "price": 3200, "ret1d": 2.1, "ret5d": 4.0,
            "ret20d": 6.5, "rsi": 58.0, "macd_hist": 15.0, "sma20": 3100, "sma50": 3000,
            "sma200": 2900, "atr": 70.0, "vol_avg20": 80_000_000, "value_avg20": 250e9,
            "tech_score": 72.0, "rel_pct": 88.0, "signal": "BELI", "reason": "Tren kuat",
            "entry": 3200, "sl": 3060, "tp": 3480,
        },
        {
            "ticker": "BBCA", "name": "Bank Central Asia", "board": "Utama",
            "trade_date": "2026-11-24", "price": 9800, "ret1d": 1.2, "ret5d": 2.5,
            "ret20d": 4.0, "rsi": 55.0, "macd_hist": 20.0, "sma20": 9600, "sma50": 9500,
            "sma200": 9200, "atr": 160.0, "vol_avg20": 45_000_000, "value_avg20": 440e9,
            "tech_score": 68.0, "rel_pct": 91.0, "signal": "BELI", "reason": "Akumulasi",
            "entry": 9800, "sl": 9500, "tp": 10300,
        },
        {
            "ticker": "BRIS", "name": "Bank Syariah Indonesia", "board": "Utama",
            "trade_date": "2026-11-24", "price": 2700, "ret1d": -1.5, "ret5d": -2.0,
            "ret20d": -5.0, "rsi": 39.0, "macd_hist": -12.0, "sma20": 2800, "sma50": 2850,
            "sma200": 2600, "atr": 65.0, "vol_avg20": 20_000_000, "value_avg20": 54e9,
            "tech_score": 25.0, "rel_pct": 30.0, "signal": "JUAL", "reason": "Downtrend",
            "entry": 2700, "sl": 2570, "tp": 2960,
        },
    ]
    store_scan.save_market_scan(rows, {"trade_date": "2026-11-24", "scanned_at": "2026-11-24T16:20:00"})

    def _fund_for(sym: str) -> dict:
        if sym == "BRIS":
            return {**_sample_fund("Financials"), "per": -5.0, "roe": -2.0}
        return _sample_fund("Communication Services")

    monkeypatch.setattr(fundamental, "_fetch_uncached", _fund_for)

    syar_beli = dm.load_sinyal_rows("BELI", syariah_only=True)
    tickers_beli = {r["ticker"] for r in syar_beli["rows"]}
    assert "TLKM" in tickers_beli
    assert "BBCA" not in tickers_beli
    assert syar_beli["total_matched"] >= 1
    assert isinstance(syar_beli["sectors"], list) and syar_beli["sectors"]

    search_hit = dm.load_sinyal_rows("ALL", search="telkom")
    assert [r["ticker"] for r in search_hit["rows"]] == ["TLKM"]

    qual = dm.load_sinyal_rows("ALL", quality_only=True)
    qual_tickers = {r["ticker"] for r in qual["rows"]}
    assert "TLKM" in qual_tickers


def test_build_paket_picks_syariah_respects_alloc(monkeypatch, tmp_store):
    rows = [
        {
            "ticker": "TLKM", "name": "Telkom Indonesia", "board": "Utama",
            "trade_date": "2026-11-24", "price": 3200, "ret1d": 1.0, "ret5d": 3.0,
            "ret20d": 5.0, "rsi": 54.0, "macd_hist": 10.0, "sma20": 3150, "sma50": 3050,
            "sma200": 2950, "atr": 70.0, "vol_avg20": 50_000_000, "value_avg20": 160e9,
            "tech_score": 70.0, "rel_pct": 85.0, "signal": "BELI", "reason": "Tren naik",
            "entry": 3200, "sl": 3060, "tp": 3480,
        },
        {
            "ticker": "ASII", "name": "Astra International", "board": "Utama",
            "trade_date": "2026-11-24", "price": 5100, "ret1d": 0.8, "ret5d": 2.0,
            "ret20d": 3.5, "rsi": 52.0, "macd_hist": 8.0, "sma20": 5000, "sma50": 4900,
            "sma200": 4700, "atr": 110.0, "vol_avg20": 30_000_000, "value_avg20": 153e9,
            "tech_score": 68.0, "rel_pct": 82.0, "signal": "BELI", "reason": "Konsisten",
            "entry": 5100, "sl": 4880, "tp": 5540,
        },
        {
            "ticker": "BBCA", "name": "Bank Central Asia", "board": "Utama",
            "trade_date": "2026-11-24", "price": 9800, "ret1d": 1.5, "ret5d": 3.2,
            "ret20d": 4.2, "rsi": 56.0, "macd_hist": 18.0, "sma20": 9600, "sma50": 9450,
            "sma200": 9100, "atr": 150.0, "vol_avg20": 40_000_000, "value_avg20": 392e9,
            "tech_score": 75.0, "rel_pct": 92.0, "signal": "BELI", "reason": "Pemimpin sektor",
            "entry": 9800, "sl": 9500, "tp": 10300,
        },
    ]
    store_scan.save_market_scan(rows, {"trade_date": "2026-11-24"})
    monkeypatch.setattr(fundamental, "_fetch_uncached", lambda _sym: _sample_fund())
    monkeypatch.setattr(newsmod, "fetch_news", lambda _sym, limit=8: [])

    res = dm.build_paket_picks(25_000_000, chat_id=0, max_plans=3, syariah_only=True)
    assert res["ok"] is True
    assert len(res["plans"]) >= 1
    assert all(p.get("syariah") is True for p in res["plans"])
    assert all(p.get("ticker") != "BBCA" for p in res["plans"])
    assert res["total_alloc_rp"] > 0
    assert res["cash_left_rp"] >= 0
    assert res["total_risk_rp"] > 0


def test_portfolio_risk_summary_execution_and_journal_mae_alpha(monkeypatch, tmp_store, uptrend_df):
    store_profile.set_syariah_mode(0, True)
    assert store_profile.get_syariah_mode(0) is True
    store_portfolio.set_risk_limits(0, 0.25, 0.45)

    buy_res = dh.record_desktop_execution(
        0, "TLKM", "BELI", 4, 2400, trade_date="2024-06-03", sl=2250, tp=2900, note="entry awal",
    )
    assert buy_res["ticker"] == "TLKM"
    sell_res = dh.record_desktop_execution(
        0, "TLKM", "JUAL", 2, 2850, trade_date="2024-09-02", note="tp1",
    )
    assert sell_res["net_pnl"] is not None

    monkeypatch.setattr(
        datamod,
        "fetch_history",
        lambda sym, period="1y", raw_symbol=False: uptrend_df.copy(),
    )
    monkeypatch.setattr(
        datamod,
        "fetch_cached_history",
        lambda sym, period="1y", raw_symbol=False: uptrend_df.copy(),
    )
    monkeypatch.setattr(newsmod, "fetch_news", lambda _sym, limit=8: [])

    port_rows = dm.evaluate_portfolio_rows(0)
    assert len(port_rows) == 1
    assert port_rows[0]["lots"] == 2
    assert port_rows[0]["value_rp"] > 0

    summary = dm.compute_portfolio_risk_summary(0, port_rows)
    assert summary["n_positions"] == 1
    assert summary["total_value_rp"] == port_rows[0]["value_rp"]
    assert summary["max_pos_pct"] == 0.25
    assert summary["max_pos_limit_pct"] == 25.0
    assert summary["max_sec_limit_pct"] == 45.0
    assert summary["risk_to_sl_rp"] >= 0

    jurnal = dm.build_journal_summary(0, limit=20)
    assert jurnal["ok"] is True
    assert jurnal["kpis"]["n_exec"] == 2
    assert jurnal["kpis"]["n_closed"] == 1
    assert jurnal["diag"]["n_with_ohlcv"] >= 1
    assert jurnal["diag"]["mean_alpha_pct"] is not None
    assert "TLKM" in jurnal["history_text"]


def test_csv_export_order_ticket_and_desktop_helpers(tmp_path):
    csv_path = tmp_path / "out.csv"
    dm.export_rows_to_csv(
        str(csv_path),
        ["Ticker", "Catatan"],
        [["TLKM", "=1+1"], ["ASII", "+cmd"], ["BRIS", "-5"], ["ITMG", "@SUM(A1:A2)"]],
    )
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Ticker", "Catatan"]
    assert rows[1] == ["TLKM", "'=1+1"]
    assert rows[2] == ["ASII", "'+cmd"]
    assert rows[3] == ["BRIS", "'-5"]
    assert rows[4] == ["ITMG", "'@SUM(A1:A2)"]

    ticket = dm.format_order_ticket_text([
        {
            "ticker": "TLKM", "grade": "A", "lots": 5, "entry": 3200,
            "sl": 3060, "tp": 3480, "buy_cost": 1_602_400, "entry_pullback": 3150,
        }
    ])
    assert "TLKM" in ticket and "5 lot" in ticket and "Rp3.200" in ticket
    assert "Tidak ada rencana order" in dm.format_order_ticket_text([])

    assert dh.DESKTOP_CHAT_ID == "desktop"
    assert dh._news_item_tag({"sent": 0.4}) == "POSITIF"
    assert dh._news_item_tag({"sent": -0.35}) == "NEGATIF"
    assert dh._news_item_tag({"sent": 0.0}) == "NETRAL"
    assert dh._news_item_tag({}) == "NETRAL"
    assert dh._fmt_rp(-1_500_000) == "-Rp1.500.000"
    assert dh._fmt_rp(float("nan")) == "-"
    assert dh._fmt_arrow_pct(2.5) == "▲ +2.50%"
    assert dh._fmt_arrow_pct(-1.25) == "▼ -1.25%"
    assert dh._fmt_arrow_pct(0.0) == "0.00%"
    assert dh._parse_sort_key("Rp1.200.000", "num") > dh._parse_sort_key("Rp950.000", "num")
    assert dh._parse_sort_key("▲ +3.50%", "num") > dh._parse_sort_key("▼ -1.20%", "num")
    assert dh._parse_sort_key("-", "num")[0] == 1

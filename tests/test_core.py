"""Unit test inti SahamBuddy — tanpa jaringan dan tanpa token bot."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
import types

import numpy as np
import pandas as pd
import pytest

# core.news -> core.data -> yfinance: stub supaya test tidak butuh jaringan
if "yfinance" not in sys.modules:
    _yf = types.ModuleType("yfinance")
    _yf.download = lambda *a, **k: None
    _yf.Ticker = lambda *a, **k: None
    sys.modules["yfinance"] = _yf

from core import (alerts, analysis, bandar, data, learn, marketcal,  # noqa: E402
                  news, powerscore, saran, store, textutil)


@pytest.fixture(autouse=True)
def _clean_caches():
    data.clear_cache()
    yield
    data.clear_cache()


def make_df(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series(6000 + np.cumsum(rng.normal(0, 15, n)), index=idx)
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
    }, index=idx)


# ---------------------------------------------------------------- alerts ---

def test_alert_triggers_are_list_and_match_kinds_works():
    ind = analysis.compute_indicators(make_df())
    triggered = alerts.check_triggers(ind)
    assert isinstance(triggered, list)
    # bug lama: `list & set` -> TypeError, scanner berhenti total
    assert alerts.match_kinds(triggered, {"jump", "drop"}) == set(triggered) & {"jump", "drop"}


def test_breakout_can_fire():
    ind = analysis.compute_indicators(make_df(n=120))
    jump = float(ind["high"].iloc[-21:-1].max()) * 1.05
    ind.iloc[-1, ind.columns.get_loc("close")] = jump
    ind.iloc[-1, ind.columns.get_loc("high")] = jump
    assert "breakout" in alerts.check_triggers(ind)


def test_breakdown_can_fire():
    ind = analysis.compute_indicators(make_df(n=120))
    crash = float(ind["low"].iloc[-21:-1].min()) * 0.95
    ind.iloc[-1, ind.columns.get_loc("close")] = crash
    ind.iloc[-1, ind.columns.get_loc("low")] = crash
    assert "breakdown" in alerts.check_triggers(ind)


# ------------------------------------------------------------ powerscore ---

def test_powerscore_normalizes_missing_components():
    # komponen hilang tidak lagi dianggap nol (skor maksimum dulu cuma 50%)
    assert powerscore.combine(100, None, None)["pct"] == 100
    assert powerscore.combine(100, 0, 0)["pct"] == 50
    assert powerscore.combine(0, 0, 0)["pct"] == 0


def test_powerscore_ratings():
    assert powerscore.combine(100, 100, 100)["rating"] == "STRONG BUY"
    assert powerscore.combine(-100, -100, -100)["rating"] == "STRONG SELL"


# --------------------------------------------------------------- sizing ----

def test_position_size_one_percent_risk():
    size = saran.position_size(10_000_000, 1000, 900)
    assert size["budget"] == 100_000
    assert size["risk"] == 100
    assert size["lots"] == 10
    assert size["value"] == 1_000_000
    assert size["real_risk"] == 100_000


def test_position_size_too_small_returns_zero_lots():
    assert saran.position_size(100_000, 5000, 4900)["lots"] == 0


def test_pnl_includes_trading_fees():
    gross = (1040 / 1000 - 1) * 100
    assert 3.0 < saran.pnl_pct(1000, 1040) < gross


# -------------------------------------------------------------- analysis ---

def test_analyze_rejects_very_short_history():
    with pytest.raises(ValueError):
        analysis.analyze("NEW", make_df(n=20))


def test_analyze_notes_short_history_and_bounds_score():
    res = analysis.analyze("MID", make_df(n=120))
    assert "Riwayat harga" in res["history_note"]
    assert -100 <= res["score"]["pct"] <= 100
    assert res["sr"]["resistance"] > res["sr"]["support"]
    assert res["snap"]["as_of"]


def test_learn_covers_current_signal_labels():
    assert learn.signal_note("Harga di dekat/atas puncak 20 hari")
    assert learn.signal_note("Belum di dekat puncak 20 hari")
    assert learn.signal_note("Harga di atas MA50")


# ----------------------------------------------------------------- news ----

def test_news_lexicon_sentiment():
    assert news.score_title("Laba BBCA anjlok")[0] < 0
    assert news.score_title("Harga tidak naik")[0] <= 0
    assert news.score_title("Saham melejit dan laba tumbuh")[0] > 0


def test_news_sentiment_marks_engine():
    items = [
        {"title": "Laba bank naik", "link": "", "date": None, "source": "X"},
        {"title": "Saham anjlok", "link": "", "date": None, "source": "Y"},
    ]
    out = news.news_sentiment(items)
    assert out["n"] == 2
    assert all(d["engine"] in {"ml", "lexicon"} for d in out["details"])


# ---------------------------------------------------------------- store ----

def _isolate(tmp_path, monkeypatch, legacy: str = "none.json"):
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "store.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / legacy))


def test_store_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ok, wl = store.watchlist_add(1, "BBCA")
    assert ok and wl == ["BBCA"]
    assert store.watchlist_add(1, "BBCA")[0] is False
    assert store.watchlist_remove(1, "BBCA") == (True, [])
    store.set_modal(1, 5_000_000)
    assert store.get_modal(1) == 5_000_000
    store.set_auto(1, True)
    assert store.get_auto(1) is True
    assert store.get_auto_chats() == ["1"]
    store.set_paper_record(1, "BBCA", {"status": "open", "entry": 1000})
    assert store.get_paper(1)["BBCA"]["entry"] == 1000
    assert store.counts()["paper_open"] == 1


def test_store_migrates_legacy_json(tmp_path, monkeypatch):
    legacy = tmp_path / "store.json"
    legacy.write_text(json.dumps({
        "42": {"watchlist": ["BBCA"], "alerts": {"BBCA": ["jump"]},
               "paper": {"BBCA": {"status": "open", "entry": 1000}},
               "modal": 5_000_000, "auto": True},
    }, ensure_ascii=False), encoding="utf-8")
    _isolate(tmp_path, monkeypatch, legacy="store.json")
    assert store.get_watchlist(42) == ["BBCA"]
    assert (tmp_path / "store.json.migrated").exists()
    assert store.get_modal(42) == 5_000_000
    assert store.get_auto(42) is True
    assert store.get_alerts(42) == {"BBCA": ["jump"]}
    assert store.get_paper(42)["BBCA"]["status"] == "open"


def test_alert_state_reports_only_new_hits(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store.set_alert(1, "BBCA", {"jump", "oversold"})
    assert store.update_alert_state(1, "BBCA", {"jump"}) == {"jump"}
    assert store.update_alert_state(1, "BBCA", {"jump"}) == set()
    assert store.update_alert_state(1, "BBCA", {"jump", "oversold"}) == {"oversold"}
    assert store.update_alert_state(1, "BBCA", set()) == set()
    assert store.get_alerts(1) == {"BBCA": ["jump", "oversold"]}


# ---------------------------------------------------------------- saran ----

def _analyzed(ticker: str = "BBCA", power: float = 60, ret20d: float = 1.0,
              sector: str = "Financials") -> dict:
    ind = pd.DataFrame({"atr14": [20.0]})
    return {
        "res": {"ticker": ticker,
                "score": {"pct": power},
                "snap": {"close": 1000.0, "ret20d": ret20d, "ret1d": 0.0},
                "ind": ind},
        "fund": None, "nsent": None, "fund_raw": {"sector": sector},
    }


def _candidate() -> dict:
    a = _analyzed()
    return saran.build_candidate(a["res"], None, None, 10_000_000, a["fund_raw"])


def _bars() -> pd.DataFrame:
    idx = pd.to_datetime(["2026-11-05", "2026-11-06", "2026-11-09"])
    return pd.DataFrame({
        "open": [950.0, 970.0, 1000.0],
        "high": [1000.0, 1010.0, 1030.0],
        "low": [940.0, 960.0, 990.0],
        "close": [990.0, 1000.0, 1020.0],
        "volume": [1e6, 1e6, 1e6],
    }, index=idx)


def test_queue_then_fill_at_next_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert saran.queue_paper(1, [_candidate()], "2026-11-06") == 1
    assert store.get_paper(1)["BBCA"]["status"] == "pending"
    msgs = saran.fill_pending(1, {"BBCA": _bars()}, 10_000_000)
    rec = store.get_paper(1)["BBCA"]
    assert rec["status"] == "open"
    assert rec["entry"] == 1000.0          # open 9 Nov, bukan close 6 Nov
    assert rec["date"] == "09 Nov"
    assert msgs and "terisi" in msgs[0]


def test_update_paper_hits_tp_with_fees(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    saran.queue_paper(1, [_candidate()], "2026-11-06")
    saran.fill_pending(1, {"BBCA": _bars()}, 10_000_000)
    msgs = saran.update_paper(1, {"BBCA": 1100.0}, today=dt.date(2026, 11, 10))
    rec = store.get_paper(1)["BBCA"]
    assert rec["status"] == "closed" and rec["hit"] == "tp"
    assert rec["exit_price"] == 1040
    assert "setelah biaya" in msgs[0]


def test_update_paper_timeout(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    saran.queue_paper(1, [_candidate()], "2026-11-06")
    saran.fill_pending(1, {"BBCA": _bars()}, 10_000_000)
    saran.update_paper(1, {"BBCA": 1005.0}, today=dt.date(2027, 1, 15))
    assert store.get_paper(1)["BBCA"]["hit"] == "timeout"


def test_pick_respects_portfolio_limits():
    analyzed = [_analyzed("AAAA"), _analyzed("BBBB"), _analyzed("CCCC")]
    full = {f"X{i}": {"sector": "Tech"} for i in range(saran.MAX_OPEN)}
    assert saran.pick(analyzed, 10_000_000, full)["picks"] == []
    sectors = {"X1": {"sector": "Financials"}, "X2": {"sector": "Financials"}}
    res = saran.pick([_analyzed("AAAA", sector="Financials"),
                      _analyzed("BBBB", sector="Energy")],
                     10_000_000, sectors)
    assert [p["ticker"] for p in res["picks"]] == ["BBBB"]


def test_pick_skips_momentum_trap():
    assert saran.pick([_analyzed("AAAA", ret20d=25.0)], 10_000_000, {})["picks"] == []


# ------------------------------------------------------------- calendar ----

def test_calendar_weekend_and_last_trading_day():
    assert not marketcal.is_trading_day(dt.date(2026, 11, 7))   # Sabtu
    assert not marketcal.is_trading_day(dt.date(2026, 11, 8))   # Minggu
    assert marketcal.last_trading_day(dt.date(2026, 11, 8)) == dt.date(2026, 11, 6)


def test_calendar_extra_holiday_override(monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS", "2026-11-09")
    monkeypatch.setenv("MARKET_HOLIDAYS_FILE", "file-tidak-ada.json")
    marketcal.extra_holidays.cache_clear()
    try:
        assert not marketcal.is_trading_day(dt.date(2026, 11, 9))
        assert marketcal.is_trading_day(dt.date(2026, 11, 6))
    finally:
        marketcal.extra_holidays.cache_clear()


def test_market_open_bounds():
    wib = marketcal.WIB
    assert marketcal.market_open(dt.datetime(2026, 11, 6, 10, 0, tzinfo=wib))
    assert not marketcal.market_open(dt.datetime(2026, 11, 6, 8, 30, tzinfo=wib))
    assert not marketcal.market_open(dt.datetime(2026, 11, 7, 10, 0, tzinfo=wib))


# ------------------------------------------------------------- textutil ----

def test_split_message_keeps_all_text():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    parts = textutil.split_message(text, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "\n".join(parts) == text


def test_split_message_huge_line():
    text = "y" * 1200
    parts = textutil.split_message(text, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts) == text


# ----------------------------------------------------------------- data ----

def _ohlc(n: int = 80) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-05", periods=n)
    close = pd.Series(np.linspace(1000, 1100, n), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.01,
                         "low": close * 0.99, "close": close,
                         "volume": 1_000_000.0}, index=idx)


def test_fetch_history_uses_memory_cache(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_download(*a, **k):
        calls["n"] += 1
        return _ohlc()

    monkeypatch.setattr(data.yf, "download", fake_download)
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_TTL_SECONDS", 300)
    data.clear_cache()
    first = data.fetch_history("BBCA", "1y")
    second = data.fetch_history("BBCA", "1y")
    assert calls["n"] == 1
    assert first.attrs["as_of"] == second.attrs["as_of"]
    assert first.attrs["as_of"]
    assert data.cache_stats()["hit"] >= 1


def test_validate_drops_impossible_ohlc():
    df = pd.DataFrame({"open": [1.0, 1.0], "high": [1.0, 0.5],
                       "low": [0.9, 1.0], "close": [1.0, 1.0],
                       "volume": [10.0, 10.0]},
                      index=pd.bdate_range("2026-01-05", periods=2))
    assert len(data._validate(df, "TEST")) == 1


# ------------------------------------------------- Tier A: skor NaN netral ---

def _row(**kw) -> pd.Series:
    base = {"close": 100.0, "rsi14": 60.0, "ret1d": 1.0, "sma20": 95.0,
            "sma50": 90.0, "sma200": float("nan"), "macd": 1.0,
            "macd_signal": 0.5, "high20": 99.0, "volume": 2e6, "vol_avg20": 1e6}
    base.update(kw)
    return pd.Series(base)


def test_missing_ma200_is_neutral_not_bear():
    s = analysis.score_from_rows(_row(), _row())
    msgs = " ".join(s["bull"] + s["bear"])
    assert "MA200" not in msgs          # tidak ada sinyal karangan saat MA200 NaN
    # 90 - 15 (MA200) - 10 (MA50>MA200) = 65, bukan 65 - 25 = 40
    assert s["score"] == 65
    assert s["pct"] == pytest.approx(65 / analysis.MAX_SCORE * 100)


# ---------------------------------------------- Tier A: TP/SL intraday ------

def test_update_paper_detects_intraday_tp(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    saran.queue_paper(1, [_candidate()], "2026-11-06")
    saran.fill_pending(1, {"BBCA": _bars()}, 10_000_000)
    assert store.get_paper(1)["BBCA"]["tp"] == 1040
    bar = pd.DataFrame({"open": [1005.0], "high": [1060.0], "low": [995.0],
                        "close": [1000.0], "volume": [1e6]},
                       index=pd.to_datetime(["2026-11-10"]))
    msgs = saran.update_paper(1, {"BBCA": bar}, today=dt.date(2026, 11, 10))
    rec = store.get_paper(1)["BBCA"]
    assert rec["status"] == "closed" and rec["hit"] == "tp"
    assert rec["exit_price"] == 1040
    assert msgs and "setelah biaya" in msgs[0]


def test_update_paper_same_bar_sl_and_tp_is_conservative(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    saran.queue_paper(1, [_candidate()], "2026-11-06")
    saran.fill_pending(1, {"BBCA": _bars()}, 10_000_000)
    bar = pd.DataFrame({"open": [1000.0], "high": [1060.0], "low": [950.0],
                        "close": [1005.0], "volume": [1e6]},
                       index=pd.to_datetime(["2026-11-10"]))
    saran.update_paper(1, {"BBCA": bar}, today=dt.date(2026, 11, 10))
    assert store.get_paper(1)["BBCA"]["hit"] == "sl"


# ------------------------------------------------- Tier A: init store ------

def test_store_schema_initialized_once(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    first = store.STORE_DB
    store.counts()
    assert store._ready_for == first
    other = str(tmp_path / "lain.db")
    monkeypatch.setattr(store, "STORE_DB", other)
    store.set_modal(9, 1_000_000)          # DB baru → skema dibuat lagi
    assert store._ready_for == other
    assert store.get_modal(9) == 1_000_000


# ------------------------------------------------ Tier A: cache berita -----

def test_fetch_news_is_cached(monkeypatch):
    rss = (b"<rss><channel><item><title>Laba bank naik</title><link>http://x</link>"
           b"<pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate><source>Kontan</source>"
           b"</item></channel></rss>")
    calls = {"n": 0}

    def fake_get(url, timeout=15):
        calls["n"] += 1
        return rss

    monkeypatch.setattr(news, "_http_get", fake_get)
    monkeypatch.setattr(news.datamod, "fetch_quote_name", lambda t: "Bank Central")
    monkeypatch.setattr(news, "_NEWS_TTL", 900)
    news.clear_cache()
    try:
        first = news.fetch_news("BBCA")
        second = news.fetch_news("BBCA")
        assert calls["n"] == 1              # permintaan kedua dari cache
        assert first == second
        assert first[0]["title"] == "Laba bank naik"
        first[0]["title"] = "diubah"        # salinan, cache tidak ikut berubah
        assert news.fetch_news("BBCA")[0]["title"] == "Laba bank naik"
    finally:
        news.clear_cache()


# ------------------------------------------- Tier B: cache data BEI --------

_BEI_ROWS = [{"StockCode": "BBCA", "ClosingPrice": 9000, "PreviousPrice": 8900},
             {"StockCode": "BBRI", "ClosingPrice": 5000, "PreviousPrice": 5100}]


def _patch_bandar(monkeypatch, payload):
    monkeypatch.setattr(bandar, "_scraper", lambda: None)
    monkeypatch.setattr(
        bandar, "_get",
        lambda url, scraper, params=None, tries=3, pause=4.0: payload(params))


def test_bandar_stock_summary_is_cached(monkeypatch):
    calls = {"n": 0}

    def payload(params):
        calls["n"] += 1
        return {"data": _BEI_ROWS}

    bandar.clear_cache()
    _patch_bandar(monkeypatch, payload)
    try:
        first = bandar.get_stock_summary("2026-11-06", max_stocks=10)
        second = bandar.get_stock_summary("2026-11-06", max_stocks=10)
        assert calls["n"] == 1              # permintaan kedua dari cache
        assert first == second
        first[0]["ClosingPrice"] = 1        # salinan: cache tidak ikut berubah
        assert bandar.get_stock_summary("2026-11-06", max_stocks=10)[0]["ClosingPrice"] == 9000
        bandar.clear_cache()
        assert bandar.cache_stats()["entries"] == 0
        assert bandar.get_stock_summary("2026-11-06", max_stocks=10)
        assert calls["n"] == 2              # cache dibersihkan → ambil ulang
    finally:
        bandar.clear_cache()


def test_bandar_cache_key_includes_trade_date(monkeypatch):
    seen: list = []

    def payload(params):
        seen.append(params.get("date"))
        return {"data": []}

    bandar.clear_cache()
    _patch_bandar(monkeypatch, payload)
    try:
        bandar.get_stock_summary("2026-11-05")
        bandar.get_stock_summary("2026-11-06")
        bandar.get_stock_summary("2026-11-06")
        assert seen == ["2026-11-05", "2026-11-06"]
    finally:
        bandar.clear_cache()


# ------------------------------------- Tier B: pemindaian /saran paralel ----

def _scan_candidate(t: str) -> dict:
    return {"res": {"ticker": t, "score": {"pct": 60},
                    "snap": {"close": 1000.0, "ret20d": 1.0, "ret1d": 0.0},
                    "ind": pd.DataFrame({"atr14": [20.0]})},
            "fund": None, "nsent": None, "fund_raw": {"sector": "Financials"}}


def test_saran_scan_is_parallel_ordered_and_error_isolated(tmp_path, monkeypatch):
    import bot

    _isolate(tmp_path, monkeypatch)
    active = {"now": 0, "max": 0}
    finished: list[str] = []

    async def fake_candidate(t):
        if t == "RUSAK":
            raise RuntimeError("sumber gagal")
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        finished.append(t)
        return _scan_candidate(t)

    monkeypatch.setattr(bot, "gather_candidate", fake_candidate)
    monkeypatch.setattr(bot, "SCAN_PAUSE", 0)
    monkeypatch.setattr(bot.saranmod, "queue_paper", lambda *a, **k: 0)
    monkeypatch.setattr(bot.saranmod, "update_paper", lambda *a, **k: [])
    monkeypatch.setattr(bot.regime, "fetch_ihsg_regime",
                        lambda: {"status": "BULL", "exposure_scale": 1.0,
                                 "sma50": 7300.0, "sma200": 7000.0, "gap_pct": 4.3})
    universe = ["AAAA", "RUSAK", "BBBB", "CCCC", "DDDD", "EEEE"]
    txt = asyncio.run(bot.saran_scan_text(1, universe))
    assert txt and "AAAA" in txt
    assert "RUSAK" not in txt
    assert active["max"] > 1                      # benar-benar paralel
    assert active["max"] <= bot.SCAN_CONCURRENCY   # tapi tetap dibatasi
    assert sorted(finished) == ["AAAA", "BBBB", "CCCC", "DDDD", "EEEE"]
    assert finished.index("AAAA") < finished.index("EEEE")  # urutan universe dijaga

    monkeypatch.setattr(bot.regime, "fetch_ihsg_regime",
                        lambda: {"status": "DEFENSIVE", "exposure_scale": 0.5,
                                 "sma50": 6800.0, "sma200": 7200.0, "gap_pct": -5.56})
    txt_def = asyncio.run(bot.saran_scan_text(1, universe))
    assert txt_def and "Rezim IHSG defensif" in txt_def and "risiko 0.5%/posisi" in txt_def


# -------------------------------- Tier B: riwayat paper trading dibatasi ----

def test_prune_paper_keeps_newest_closed_and_survivors(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    for i in range(25):
        store.set_paper_record(1, f"C{i:02d}", {
            "status": "closed", "hit": "tp", "entry": 1000.0, "exit_price": 1040.0,
            "filled_at": f"2026-{1 + i // 20:02d}-{1 + i % 20:02d}"})
    store.set_paper_record(1, "OPEN1", {"status": "open", "entry": 1000.0})
    store.set_paper_record(1, "PEND1", {"status": "pending", "entry": 1000.0})
    assert store.prune_paper(1, keep_closed=20) == 5
    paper = store.get_paper(1)
    assert len([s for s, r in paper.items() if r["status"] == "closed"]) == 20
    assert {"OPEN1", "PEND1"} <= set(paper)
    assert "C00" not in paper and "C04" not in paper   # terlama dibuang
    assert "C05" in paper and "C24" in paper           # terbaru disimpan
    assert store.prune_paper(1, keep_closed=20) == 0    # idempoten


def test_quotes_forgotten_caps_closed_lines(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    for i in range(saran.CLOSED_SHOWN + 4):
        store.set_paper_record(1, f"C{i:02d}", {"status": "closed", "hit": "tp",
                                                "entry": 1000.0, "exit_price": 1040.0})
    lines = saran.quotes_forgotten(1)
    assert sum("🎯 TP" in ln for ln in lines) == saran.CLOSED_SHOWN
    assert any("4 posisi selesai" in ln for ln in lines)


# ------------------------------- Tier B: biaya backtest = biaya /saran -----

def test_backtest_and_paper_share_trading_fees():
    import backtest

    assert backtest.pnl_pct is saran.pnl_pct        # satu sumber rumus biaya
    assert (saran.BUY_FEE, saran.SELL_FEE) == (0.0015, 0.0025)
    assert backtest.BUY_FEE == saran.BUY_FEE
    assert backtest.SELL_FEE == saran.SELL_FEE
    gross = (1040 / 1000 - 1) * 100
    net = backtest.pnl_pct(1000, 1040)              # forward return backtest = net
    assert net < gross


def test_backtest_real_commission_matches_saran_per_side():
    pytest.importorskip("backtesting")
    from backtest_real import commission

    assert commission(100, 1000.0) == saran.BUY_FEE     # beli
    assert commission(-100, 1000.0) == saran.SELL_FEE   # jual (ada pajak)

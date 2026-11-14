"""Tes offline screener pasar penuh (tanpa Yahoo)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import screener, store
from tests.conftest import make_prices


def _universe_frames(n: int = 10, bars: int = 120) -> dict[str, pd.DataFrame]:
    """Deret harga sintetis yang cukup untuk peringkat relatif."""
    return {
        f"T{i:02d}": make_prices(np.linspace(1000, 1000 + i * 25, bars))
        for i in range(n)
    }


def _batch_df(batch: list[str], short: set[str] | None = None) -> pd.DataFrame:
    """Tiruan hasil `yf.download` multi-index untuk beberapa ticker."""
    short = short or set()
    parts = {}
    for sym in batch:
        n = 5 if sym in short else 60
        df = make_prices(np.linspace(1000, 1200, n)).rename(
            columns=str.title)
        parts[f"{sym}.JK"] = df
    return pd.concat(parts, axis=1)


# ------------------------------------------------------------------ _slice
def test_slice_reads_multiindex_frame():
    raw = _batch_df(["BBCA"])
    sub = screener._slice(raw, "BBCA.JK")
    assert sub is not None
    assert list(sub.columns) == ["open", "high", "low", "close", "volume"]
    assert len(sub) == 60


def test_slice_reads_flat_single_ticker_frame():
    flat = make_prices(np.linspace(1000, 1200, 60))
    sub = screener._slice(flat, "TLKM.JK")
    assert sub is not None and len(sub) == 60


def test_slice_rejects_missing_and_short_data():
    assert screener._slice(None, "BBCA.JK") is None
    assert screener._slice(_batch_df(["X"]), "MISSING.JK") is None
    assert screener._slice(_batch_df(["X"], short={"X"}), "X.JK") is None


# --------------------------------------------------------------- classify
@pytest.mark.parametrize("kwargs,expected", [
    (dict(close=120, sma20=115, sma50=110, rsi_v=60, macd_hist=0.5,
          ret20d=5, rel_pct=85, rel_quintil=5, rel_ok=True, tech_pct=50),
     "BELI"),
    (dict(close=100, sma20=105, sma50=110, rsi_v=35, macd_hist=-0.5,
          ret20d=-8, rel_pct=15, rel_quintil=1, rel_ok=True, tech_pct=-50),
     "JUAL"),
    # sudah naik 20% dalam 20 hari -> jangan dikejar
    (dict(close=120, sma20=115, sma50=110, rsi_v=60, macd_hist=0.5,
          ret20d=20, rel_pct=90, rel_quintil=5, rel_ok=True, tech_pct=50),
     "TAHAN"),
    # RSI overbought -> bukan kandidat beli
    (dict(close=120, sma20=115, sma50=110, rsi_v=82, macd_hist=0.5,
          ret20d=5, rel_pct=90, rel_quintil=5, rel_ok=True, tech_pct=50),
     "TAHAN"),
    # riwayat < 50 bar -> tren belum terbentuk
    (dict(close=120, sma20=None, sma50=None, rsi_v=60, macd_hist=0.5,
          ret20d=5, rel_pct=90, rel_quintil=5, rel_ok=True, tech_pct=50),
     "TAHAN"),
])
def test_classify_signals(kwargs, expected):
    sig, reason = screener.classify(**kwargs)
    assert sig == expected
    assert reason


def test_classify_chase_reason_is_honest():
    sig, reason = screener.classify(
        120, 115, 110, 60, 0.5, 20, 90, 5, True, 50)
    assert sig == "TAHAN"
    assert "dikejar" in reason


def test_classify_fallback_uses_technical_score_when_relative_unavailable():
    buy, _ = screener.classify(
        120, 115, 110, 60, 0.5, 5, None, None, False, 50)
    sell, _ = screener.classify(
        100, 105, 110, 35, -0.5, -8, None, None, False, -50)
    assert buy == "BELI"
    assert sell == "JUAL"


# --------------------------------------------------------------- evaluate
def test_evaluate_skips_short_history_and_attaches_names():
    frames = {
        "AAAA": make_prices(np.linspace(1000, 1200, 120)),
        "SHORT": make_prices(np.linspace(1000, 1010, 10)),
    }
    rows, skipped = screener.evaluate(frames, names={"AAAA": "PT Contoh"})
    assert skipped == ["SHORT"]
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "AAAA"
    assert row["name"] == "PT Contoh"
    assert row["signal"] in screener.SIGNALS
    assert row["price"] and row["trade_date"] and row["reason"]
    assert row["value_avg20"] and row["vol_avg20"]


def test_evaluate_buy_rows_get_entry_sl_tp(monkeypatch):
    monkeypatch.setattr(screener, "classify",
                        lambda *a, **k: ("BELI", "uji"))
    rows, _ = screener.evaluate(_universe_frames())
    assert rows
    for r in rows:
        assert r["signal"] == "BELI"
        assert r["entry"] and r["sl"] and r["tp"]
        assert r["sl"] < r["entry"] < r["tp"]


def test_evaluate_returns_empty_when_no_usable_frames():
    rows, skipped = screener.evaluate({})
    assert rows == [] and skipped == []


# ------------------------------------------------------------ fetch batch
def test_fetch_frames_chunks_and_skips_short(monkeypatch):
    calls = []

    def fake_download(batch, period):
        calls.append(list(batch))
        return _batch_df(batch, short={"SHORT"})

    monkeypatch.setattr(screener, "_download_chunk", fake_download)
    progress = []
    frames = screener.fetch_frames(
        ["AAAA", "SHORT", "BBBB"], chunk=2, pause=0, retries=0,
        progress=lambda d, t, ok: progress.append((d, t, ok)))
    assert calls == [["AAAA", "SHORT"], ["BBBB"]]
    assert set(frames) == {"AAAA", "BBBB"}
    assert progress[-1] == (3, 3, 2)


def test_fetch_frames_retries_failed_batch(monkeypatch):
    monkeypatch.setattr(screener.time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def flaky(batch, period):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("rate limit")
        return _batch_df(batch)

    monkeypatch.setattr(screener, "_download_chunk", flaky)
    frames = screener.fetch_frames(["AAAA"], chunk=1, pause=0, retries=1)
    assert set(frames) == {"AAAA"}
    assert attempts["n"] == 2


# ------------------------------------------------------------------- scan
def test_scan_saves_snapshot_then_uses_cache(tmp_store):
    frames = _universe_frames()
    calls = []

    def downloader(symbols):
        calls.append(list(symbols))
        return {s: frames[s] for s in symbols if s in frames}

    res = screener.scan(force=True, symbols=list(frames),
                        downloader=downloader)
    assert res["cached"] is False
    assert calls == [list(frames)]
    assert res["meta"]["ok"] == len(frames)
    assert sum(res["counts"].values()) == len(frames)
    assert store.market_scan_counts() == res["counts"]
    saved = store.load_market_scan()
    assert len(saved) == len(frames)
    assert all(r.get("value_avg20") for r in saved)
    assert store.market_scan_meta()["trade_date"]

    res2 = screener.scan()          # snapshot hari ini sudah ada
    assert res2["cached"] is True
    assert calls == [list(frames)]  # tidak mengunduh ulang


def test_scan_raises_when_nothing_downloaded(tmp_store):
    with pytest.raises(RuntimeError):
        screener.scan(force=True, symbols=["AAAA"], downloader=lambda s: {})


def test_evaluate_rejects_stale_and_bad_ohlc_and_downgrades_dormant(monkeypatch):
    monkeypatch.setattr(screener, "classify", lambda *a, **k: ("BELI", "uji"))
    live_df = make_prices(np.linspace(9000, 9500, 120), start="2026-06-01")
    stale_df = make_prices(np.linspace(1000, 1200, 60), start="2025-01-01")
    bad_df = live_df.copy()
    bad_df.iloc[-1, bad_df.columns.get_loc("high")] = bad_df.iloc[-1]["low"] - 10
    dormant_df = live_df.copy()
    dormant_df.iloc[-6:, dormant_df.columns.get_loc("volume")] = 0.0

    rows, skipped = screener.evaluate({
        "LIVE": live_df,
        "SUSP": stale_df,
        "BROKEN": bad_df,
        "SLEEP": dormant_df,
    })
    assert "SUSP" in skipped
    assert "BROKEN" in skipped
    by_t = {r["ticker"]: r for r in rows}
    assert "LIVE" in by_t
    assert by_t["LIVE"]["signal"] == "BELI"
    assert by_t["LIVE"]["entry"] % 25 == 0
    assert by_t["LIVE"]["sl"] % 25 == 0
    assert by_t["LIVE"]["tp"] % 25 == 0
    assert by_t["SLEEP"]["signal"] == "TAHAN"
    assert "tidur" in by_t["SLEEP"]["reason"].lower()


def test_evaluate_buy_levels_use_pricing_round_tick(monkeypatch):
    monkeypatch.setattr(screener, "classify", lambda *a, **k: ("BELI", "uji"))
    df = make_prices(np.linspace(8900, 9013.4, 80), start="2026-06-01")
    rows, _ = screener.evaluate({"TICK": df})
    assert len(rows) == 1
    r = rows[0]
    assert r["entry"] == 9025.0
    assert r["sl"] % 25 == 0
    assert r["tp"] % 25 == 0
    assert r["sl"] < r["entry"] < r["tp"]

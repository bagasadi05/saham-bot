"""Unit test untuk paket perbaikan: core/verdict, news_ml INT8/info, daily_snapshots, core/types."""
from __future__ import annotations

import bot
from core import news_ml, store, types, verdict


def test_verdict_reexport_sinkron_dengan_bot():
    assert bot.build_verdict is verdict.build_verdict
    assert bot._verdict_beli is verdict._verdict_beli
    assert bot._verdict_jual is verdict._verdict_jual
    assert bot._weekly_from_daily is verdict._weekly_from_daily


def test_news_ml_model_info_dan_softmax():
    info = news_ml.model_info()
    assert set(info) >= {"loaded", "failed", "engine", "quantized", "local_dir"}
    probs = news_ml._softmax_rows([[2.0, 0.0, 0.0]])[0]
    assert abs(sum(probs) - 1.0) < 1e-6
    assert probs[0] > probs[1]


def test_daily_snapshots_save_load_dan_upsert(tmp_store):
    n = store.save_daily_snapshots(
        [
            {
                "ticker": "BBCA.JK",
                "close": 9500.0,
                "volume": 50_000_000.0,
                "foreign_net": 120_000_000_000.0,
                "pe": 22.5,
                "pbv": 4.2,
                "roe": 0.21,
                "der": 0.15,
                "div_yield": 0.028,
            },
            {
                "ticker": "BBRI",
                "close": 4800.0,
                "foreign_net": -30_000_000_000.0,
                "pe": 12.1,
            },
        ],
        trade_date="2026-11-20",
    )
    assert n == 2

    # Upsert hari yang sama memperbarui nilai tanpa duplikasi
    store.save_daily_snapshots(
        [{"ticker": "BBCA", "close": 9600.0, "pe": 22.7}],
        trade_date="2026-11-20",
    )
    rows = store.load_daily_snapshots(ticker="BBCA")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "BBCA"
    assert rows[0]["close"] == 9600.0
    assert rows[0]["pe"] == 22.7

    all_rows = store.load_daily_snapshots(since="2026-11-20")
    assert {r["ticker"] for r in all_rows} == {"BBCA", "BBRI"}


def test_core_types_contracts():
    sample: types.NewsSentimentResult = {
        "label": "positive",
        "score": 0.91,
        "probs": {"positive": 0.91, "neutral": 0.06, "negative": 0.03},
    }
    assert sample["label"] == "positive"
    assert "ticker" in types.DailySnapshot.__annotations__
    assert "ticker" in types.IndicatorSnapshot.__annotations__

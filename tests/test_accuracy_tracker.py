import pandas as pd

import core.accuracy_tracker as tracker


def test_tracker_records_and_evaluates_tp1_and_unfilled(tmp_path):
    p = tmp_path / "tracker.json"
    tracker.record_recommendation(
        "KLBF", "2026-11-10", "LAYAK", 740, 760, 720, 810, 840,
        regime="DEFENSIVE", power_score=39, path=p,
    )
    tracker.record_recommendation(
        "INTP", "2026-11-10", "TUNGGU", 5600, 5700, 5450, 6200,
        regime="DEFENSIVE", power_score=35, path=p,
    )

    klbf_df = pd.DataFrame(
        {
            "open": [765, 755, 770, 790, 800, 805],
            "high": [770, 765, 785, 805, 815, 820],
            "low": [758, 750, 765, 780, 795, 798],
            "close": [760, 762, 780, 798, 812, 815],
            "volume": [1e7] * 6,
        },
        index=pd.to_datetime([
            "2026-11-11", "2026-11-12", "2026-11-13",
            "2026-11-16", "2026-11-17", "2026-11-18",
        ]),
    )
    intp_df = pd.DataFrame(
        {
            "open": [5900] * 6,
            "high": [6000] * 6,
            "low": [5850] * 6,
            "close": [5950] * 6,
            "volume": [1e6] * 6,
        },
        index=pd.to_datetime([
            "2026-11-11", "2026-11-12", "2026-11-13",
            "2026-11-16", "2026-11-17", "2026-11-18",
        ]),
    )

    def _fake_fetch(sym):
        return klbf_df if sym == "KLBF" else intp_df

    rows = tracker.evaluate_recommendations(fetch_fn=_fake_fetch, entry_expiry_days=5, horizon_days=20, path=p)
    assert len(rows) == 2
    klbf_r = [r for r in rows if r["ticker"] == "KLBF"][0]
    intp_r = [r for r in rows if r["ticker"] == "INTP"][0]

    assert klbf_r["status"] == "CLOSED"
    assert klbf_r["outcome"] == "TP1_HIT"
    assert klbf_r["filled_date"] == "2026-11-11"
    assert klbf_r["return_pct"] > 5.0

    assert intp_r["status"] == "EXPIRED"
    assert intp_r["outcome"] == "UNFILLED"

    summary = tracker.summarize_accuracy(rows)
    assert summary["total_records"] == 2
    assert summary["fill_rate_pct"] == 50.0
    assert summary["win_rate_pct"] == 100.0
    assert summary["sl_rate_pct"] == 0.0

"""Tes penyimpanan histori sinyal (scan_history) di core/store.py."""
from __future__ import annotations

from core import store


def _rows(signal="BELI"):
    return [
        {"ticker": "BBCA", "signal": signal, "price": 9000.0, "entry": 9000.0,
         "sl": 8800.0, "tp": 9400.0, "atr": 200.0, "rel_pct": 88.0,
         "reason": "tren naik"},
        {"ticker": "TLKM", "signal": "TAHAN", "price": 3000.0, "entry": None,
         "sl": None, "tp": None, "atr": 60.0, "rel_pct": 40.0, "reason": "datar"},
    ]


def test_append_creates_rows_and_counts(tmp_store):
    assert store.append_scan_history(_rows(), "2026-01-05") == 2
    counts = store.scan_history_counts()
    assert counts["total"] == 2
    assert counts["pending"] == 2
    assert counts["first"] == "2026-01-05"


def test_same_day_rescan_updates_but_keeps_results(tmp_store):
    store.append_scan_history(_rows(), "2026-01-05")
    assert len(store.pending_scan_history("2026-02-01")) == 2
    result = {"trade_date": "2026-01-05", "ticker": "BBCA", "bars": 20,
              "fwd_ret": 1.5, "r_multiple": 0.3, "outcome": "TIME"}
    assert store.close_scan_history([result]) == 1

    store.append_scan_history(_rows(signal="JUAL"), "2026-01-05")

    closed = store.closed_scan_history("2026-01-01")
    assert len(closed) == 1
    assert closed[0]["fwd_ret"] == 1.5          # hasil lama tidak hilang
    assert [r["ticker"] for r in store.pending_scan_history("2026-02-01")] == ["TLKM"]


def test_close_then_closed_history_filters_by_since(tmp_store):
    store.append_scan_history(_rows(), "2026-01-05")
    store.close_scan_history([
        {"trade_date": "2026-01-05", "ticker": "BBCA", "bars": 20,
         "fwd_ret": 2.0, "r_multiple": 2.0, "outcome": "TP"},
        {"trade_date": "2026-01-05", "ticker": "TLKM", "bars": 20,
         "fwd_ret": -0.5, "r_multiple": None, "outcome": "NA"},
    ])
    assert len(store.closed_scan_history("2026-01-01")) == 2
    assert len(store.closed_scan_history("2026-01-06")) == 0
    only_beli = store.closed_scan_history("2026-01-01", signal="BELI")
    assert [r["ticker"] for r in only_beli] == ["BBCA"]


def test_old_rows_are_pruned_but_recent_kept(tmp_store):
    store.append_scan_history(_rows(), "2025-01-01")
    store.append_scan_history(_rows(), "2026-01-05")
    assert store.scan_history_counts()["total"] == 4

    store.append_scan_history(_rows(), "2026-01-06", keep_days=30)

    counts = store.scan_history_counts()
    assert counts["first"] == "2026-01-05"     # 2025 terhapus
    assert counts["last"] == "2026-01-06"

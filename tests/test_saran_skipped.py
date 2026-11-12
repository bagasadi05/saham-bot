"""Kandidat yang gagal dibangun harus dihitung dan dicatat, bukan hilang diam-diam."""
from __future__ import annotations

import logging

from core import saran


def test_pick_reports_and_logs_skipped(monkeypatch, caplog):
    def fake_build(res, fund, news, modal=None, fund_raw=None):
        if res["ticker"] == "BAD":
            raise ValueError("snapshot kosong")
        return {"ticker": res["ticker"], "power": 50, "ret20d": 1.0,
                "rel_ok": False, "sector": None}

    monkeypatch.setattr(saran, "build_candidate", fake_build)
    analyzed = [{"res": {"ticker": "OK"}}, {"res": {"ticker": "BAD"}}]

    with caplog.at_level(logging.WARNING, logger="sahambuddy.saran"):
        result = saran.pick(analyzed, modal=10_000_000)

    assert [p["ticker"] for p in result["picks"]] == ["OK"]
    assert result["skipped"] == ["BAD"]
    assert "dilewati" in caplog.text


def test_pick_without_failures_has_empty_skipped(monkeypatch):
    monkeypatch.setattr(saran, "build_candidate", lambda *a, **k: {
        "ticker": "OK", "power": 50, "ret20d": 1.0, "rel_ok": False,
        "sector": None,
    })
    result = saran.pick([{"res": {"ticker": "OK"}}], modal=10_000_000)
    assert result["skipped"] == []

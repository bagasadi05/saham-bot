"""Regresi: screener memakai harga yang disesuaikan (auto_adjust=True)."""
from __future__ import annotations

import pandas as pd

from core import screener


def test_download_chunk_uses_adjusted_prices(monkeypatch):
    captured: dict = {}

    def fake_download(*args, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", fake_download)
    screener._download_chunk(["BBCA"], "1y")

    assert captured["auto_adjust"] is True
    # argumen lain tetap seperti semula
    assert captured["interval"] == "1d"
    assert captured["group_by"] == "ticker"

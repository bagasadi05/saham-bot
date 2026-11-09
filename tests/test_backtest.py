"""Test backtest statistik memakai data sintetis (tanpa Yahoo)."""
from __future__ import annotations

import numpy as np
import pytest

import backtest
from tests.conftest import make_prices


def test_backtest_menghasilkan_tabel(uptrend_df, monkeypatch):
    monkeypatch.setattr(backtest.data, "fetch_history", lambda *_a, **_k: uptrend_df)
    bt = backtest.backtest("BBCA")
    assert not bt.empty
    assert list(bt.columns) == ["date", "rating", "pct", "fwd_ret"]
    assert {"STRONG BUY", "BUY", "NETRAL", "SELL", "STRONG SELL"} & set(bt["rating"])


def test_summarize_memuat_baseline(uptrend_df, monkeypatch):
    monkeypatch.setattr(backtest.data, "fetch_history", lambda *_a, **_k: uptrend_df)
    txt = backtest.summarize(backtest.backtest("BBCA"))
    assert "Baseline" in txt and "win-rate" in txt


def test_backtest_menolak_data_pendek(monkeypatch):
    pendek = make_prices(np.linspace(1000, 1100, 100))
    monkeypatch.setattr(backtest.data, "fetch_history", lambda *_a, **_k: pendek)
    with pytest.raises(ValueError):
        backtest.backtest("BBCA")

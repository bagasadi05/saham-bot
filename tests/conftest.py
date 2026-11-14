"""Fixture bersama untuk test suite SahamBuddy.

Semua test WAJIB offline: tidak ada panggilan Yahoo/Google News/HuggingFace.
Data harga sintetis dibuat di sini; fungsi jaringan di-monkeypatch per test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_prices(closes, start: str = "2024-01-01", volume=1_000_000):
    """DataFrame OHLCV sintetis dari deret harga penutupan."""
    close = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(len(close), float(volume)),
        },
        index=idx,
    )


@pytest.fixture
def uptrend_df():
    return make_prices(np.linspace(1000, 3000, 320))


@pytest.fixture
def downtrend_df():
    return make_prices(np.linspace(3000, 1000, 320))


@pytest.fixture(autouse=True)
def _isolated_caches():
    from core import bandar, banding, data, fundamental, news, regime

    data.clear_cache()
    fundamental.clear_cache()
    news.clear_cache()
    bandar.clear_cache()
    banding._mkt_cache.update({"ts": 0.0, "val": None})
    regime.clear_cache()
    yield
    data.clear_cache()


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """store.db & legacy store.json di folder sementara — isolasi penuh."""
    from core import store

    db_path = tmp_path / "store.db"
    json_path = tmp_path / "store.json"
    monkeypatch.setattr(store, "STORE_DB", str(db_path))
    monkeypatch.setattr(store, "STORE_FILE", str(json_path))
    return db_path

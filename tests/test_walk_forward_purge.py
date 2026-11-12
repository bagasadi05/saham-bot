"""Purge label walk-forward: tidak boleh ada label latih yang bocor ke test."""
from __future__ import annotations

import math

import pandas as pd

from research import evaluate


def _df(days: int = 900) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=days)
    return pd.DataFrame({
        "date": dates,
        "ticker": "TEST",
        "fwd": 0.0,
        "score": 0.0,
    })


def test_purged_split_leaves_gap_for_label_horizon():
    df = _df()
    train, test = evaluate.purged_split(df, 2025, horizon=20)

    assert not train.empty and not test.empty
    horizon_days = int(math.ceil(20 * 7 / 5)) + 2
    last_train = pd.to_datetime(train["date"]).max()
    assert last_train + pd.Timedelta(days=horizon_days) < pd.Timestamp("2025-01-01")


def test_embargo_moves_cutoff_earlier_by_calendar_days():
    df = _df()
    base, _ = evaluate.purged_split(df, 2025, horizon=20, embargo_days=0)
    extra, _ = evaluate.purged_split(df, 2025, horizon=20, embargo_days=14)

    last_base = pd.to_datetime(base["date"]).max()
    last_extra = pd.to_datetime(extra["date"]).max()
    assert last_extra < last_base
    assert (last_base - last_extra).days >= 14


def test_test_set_only_contains_target_year():
    df = _df()
    _, test = evaluate.purged_split(df, 2025, horizon=20)

    assert set(pd.to_datetime(test["date"]).dt.year) == {2025}


def test_no_train_date_reaches_test_year():
    df = _df()
    train, test = evaluate.purged_split(df, 2025, horizon=20)
    first_test = pd.to_datetime(test["date"]).min()
    assert pd.to_datetime(train["date"]).max() < first_test

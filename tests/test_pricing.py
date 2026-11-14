"""Tes fraksi harga BEI dan konsistensi biaya transaksi."""
from __future__ import annotations

import pytest

import config
from core import pricing, saran


@pytest.mark.parametrize("price,tick", [
    (150, 1.0), (199, 1.0), (200, 2.0), (499, 2.0), (500, 5.0),
    (1999, 5.0), (2000, 10.0), (4999, 10.0), (5000, 25.0), (9000, 25.0),
])
def test_tick_bands(price, tick):
    assert pricing.tick_size(price) == tick


def test_round_and_ceil_tick():
    assert pricing.round_tick(9012, 25) == 9000      # 12 dari 9000, 13 dari 9025
    assert pricing.round_tick(9013, 25) == 9025
    assert pricing.round_tick(9026, 25) == 9025
    assert pricing.ceil_tick(9013, 25) == 9025
    assert pricing.round_tick(1003) == 1005      # tick 5 untuk band 500–2.000


def test_stop_and_target_are_valid_ticks():
    entry, atr = 9000.0, 300.0
    sl = saran.stop_price(entry, atr)
    tp = saran.target_price(entry, atr)
    assert sl == 8700 and tp == 9600
    assert sl % 25 == 0 and tp % 25 == 0


def test_stop_never_zero_or_above_entry():
    sl = saran.stop_price(100.0, 500.0)          # ATR lebih besar dari harga
    assert 0 < sl < 100


def test_low_price_uses_tick_one():
    assert saran.stop_price(150.0, 10.0) == 140
    assert saran.target_price(150.0, 10.0) == 170


def test_pnl_pct_matches_config_fees():
    assert config.IDX_ROUND_TRIP == pytest.approx(0.004)
    # jual di harga yang sama: hanya biaya, ± -0,399%
    assert saran.pnl_pct(1000, 1000) == pytest.approx(-0.3994, abs=1e-3)
    assert saran.BUY_FEE == config.IDX_BUY_FEE
    assert saran.SELL_FEE == config.IDX_SELL_FEE


def test_arb_floor_and_gap_risk_defaults():
    import datetime as dt
    d = dt.date(2026, 11, 2)
    assert pricing.min_board_floor(d) == 1.0
    assert pricing.min_board_floor(dt.date(2026, 9, 1)) == 50.0
    assert pricing.arb_floor_price(2000, d) == 1860.0
    risk = pricing.check_arb_gap_risk(2000, 1800, dt=d)
    assert risk["risk"] is True
    assert risk["below_floor"] is True
    assert risk["deeper_than_7pct"] is True

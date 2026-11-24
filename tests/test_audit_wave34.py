"""Tes regresi Gelombang 3 & 4 audit_sahambot.md (C1, C3, B5)."""
from __future__ import annotations

import pytest

from core import sektor, store


def test_c1_check_lot_price_tolak_nan_inf():
    with pytest.raises(ValueError):
        store._check_lot_price(10, float("nan"))
    with pytest.raises(ValueError):
        store._check_lot_price(10, float("inf"))


def test_c1_open_executions_kosong_bila_semua_posisi_sudah_terjual(tmp_store):
    store.record_execution_buy(100, "BBCA", 10, 9000.0, trade_date="2026-11-10")
    assert "BBCA" in store.open_executions_by_ticker(100)
    store.record_execution_sell(100, "BBCA", 10, 9500.0, trade_date="2026-11-12")
    assert store.real_positions(100) == []
    assert store.open_executions_by_ticker(100) == {}


def test_c3_empat_kode_sektor_resmi_bei_terpetakan():
    for code in ("IDXINDUST", "IDXNONCYC", "IDXCYCLIC", "IDXPROPERT"):
        assert code in sektor.SECTOR_NAMES

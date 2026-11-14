"""Test pemicu alert & jam bursa."""
from __future__ import annotations

import numpy as np
import pytest

import datetime as dt

from core import alerts, analysis, marketcal
from tests.conftest import make_prices


def _ind(closes, volumes=None):
    df = make_prices(closes, volume=1_000_000)
    if volumes is not None:
        df["volume"] = np.asarray(volumes, dtype=float)
    return analysis.compute_indicators(df)


def test_lonjakan_harian_memicu_jump():
    closes = list(np.linspace(1000, 1000, 60))
    closes[-1] = closes[-2] * 1.08  # +8% sehari
    trig = alerts.check_triggers(_ind(closes))
    assert "jump" in trig


def test_penurunan_tajam_memicu_drop():
    closes = list(np.linspace(1000, 1000, 60))
    closes[-1] = closes[-2] * 0.92
    assert "drop" in alerts.check_triggers(_ind(closes))


def test_lonjakan_volume_memicu_vol_spike():
    closes = list(np.linspace(1000, 1000, 60))
    vols = [1_000_000] * 59 + [5_000_000]
    assert "vol_spike" in alerts.check_triggers(_ind(closes, vols))


def test_data_pendek_tidak_memicu_apa_pun():
    assert alerts.check_triggers(_ind(np.linspace(1000, 1100, 10))) == []


def test_build_alert_message_memuat_simbol_dan_pemicu():
    teks = alerts.build_alert_message(
        "BBCA", ["jump"],
        {"close": 5200, "ret1d": 6.2},
        {"pct": 33, "emoji": "📈", "rating": "BUY"},
    )
    assert "BBCA" in teks
    assert "Lonjakan harga" in teks
    assert "Condong Menguat" in teks
    assert "BUY" not in teks
    assert "IC harian" in teks


@pytest.mark.parametrize(
    "tgl,jam,menit,harus_buka",
    [
        ((2026, 11, 2), 9, 0, True),    # Senin buka
        ((2026, 11, 6), 15, 59, True),  # Jumat sore
        ((2026, 11, 4), 16, 0, True),   # Rabu tepat tutup masih dihitung
        ((2026, 11, 3), 8, 59, False),  # Selasa sebelum buka
        ((2026, 11, 3), 16, 1, False),  # Selasa setelah tutup
        ((2026, 11, 7), 10, 0, False),  # Sabtu
        ((2026, 11, 8), 10, 0, False),  # Minggu
    ],
)
def test_market_hours(tgl, jam, menit, harus_buka):
    now = dt.datetime(*tgl, jam, menit, tzinfo=marketcal.WIB)
    assert marketcal.market_open(now) is harus_buka


def test_semua_kinds_punya_label_dan_contoh():
    assert alerts.ALL_KINDS == set(alerts.KIND_LABEL) == set(alerts.KIND_EXAMPLES)

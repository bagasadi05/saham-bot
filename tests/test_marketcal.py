"""Test kalender bursa BEI (core.marketcal) — sepenuhnya offline."""
from __future__ import annotations

import datetime as dt

import pytest

from core import marketcal


@pytest.fixture(autouse=True)
def _bersihkan_cache():
    marketcal.extra_holidays.cache_clear()
    yield
    marketcal.extra_holidays.cache_clear()


def test_libur_dari_env_dan_nilai_aneh_diabaikan(monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS", "bukan-tanggal, 2026-12-25;2026-12-26")
    monkeypatch.setenv("MARKET_HOLIDAYS_FILE", "")
    holiday = marketcal.extra_holidays()

    assert dt.date(2026, 12, 25) in holiday
    assert dt.date(2026, 12, 26) in holiday
    assert len(holiday) == 2  # nilai tidak valid tidak masuk


def test_libur_dari_file_list_dan_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS", "")
    f = tmp_path / "holidays.json"

    f.write_text('["2026-12-31"]', encoding="utf-8")
    monkeypatch.setenv("MARKET_HOLIDAYS_FILE", str(f))
    marketcal.extra_holidays.cache_clear()
    assert marketcal.extra_holidays() == frozenset({dt.date(2026, 12, 31)})

    f.write_text('{"holidays": ["2026-12-30", 123]}', encoding="utf-8")
    marketcal.extra_holidays.cache_clear()
    assert marketcal.extra_holidays() == frozenset({dt.date(2026, 12, 30)})


def test_file_libur_rusak_diabaikan(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS", "")
    f = tmp_path / "holidays.json"
    f.write_text("{bukan json", encoding="utf-8")
    monkeypatch.setenv("MARKET_HOLIDAYS_FILE", str(f))
    marketcal.extra_holidays.cache_clear()

    assert marketcal.extra_holidays() == frozenset()


def test_tanpa_exchange_calendars_pakai_weekday_dan_libur_env(monkeypatch):
    monkeypatch.setattr(marketcal, "_calendar", lambda: None)
    monkeypatch.setenv("MARKET_HOLIDAYS", "2026-11-09")
    marketcal.extra_holidays.cache_clear()

    assert marketcal.is_trading_day(dt.date(2026, 11, 6))       # Jumat
    assert not marketcal.is_trading_day(dt.date(2026, 11, 7))   # Sabtu
    assert not marketcal.is_trading_day(dt.date(2026, 11, 9))   # libur manual


def test_last_trading_day_mundur_melewati_akhir_pekan(monkeypatch):
    monkeypatch.setattr(marketcal, "_calendar", lambda: None)
    monkeypatch.setenv("MARKET_HOLIDAYS", "")
    marketcal.extra_holidays.cache_clear()

    # Minggu 8 Nov 2026 → Jumat 6 Nov 2026
    assert marketcal.last_trading_day(dt.date(2026, 11, 8)) == dt.date(2026, 11, 6)


def test_trading_days_between_menghitung_hari_bursa(monkeypatch):
    monkeypatch.setattr(marketcal, "_calendar", lambda: None)
    monkeypatch.setenv("MARKET_HOLIDAYS", "")
    marketcal.extra_holidays.cache_clear()

    assert marketcal.trading_days_between(dt.date(2026, 11, 6),
                                          dt.date(2026, 11, 9)) == 1
    assert marketcal.trading_days_between(dt.date(2026, 11, 9),
                                          dt.date(2026, 11, 9)) == 0
    assert marketcal.trading_days_between(dt.date(2026, 11, 9),
                                          dt.date(2026, 11, 6)) == 0


@pytest.mark.parametrize("jam,menit,harus_buka", [
    (8, 59, False),
    (9, 0, True),
    (15, 59, True),
    (16, 0, True),     # inklusif sampai 16:00
    (16, 1, False),
])
def test_market_open_batas_jam(jam, menit, harus_buka, monkeypatch):
    monkeypatch.setattr(marketcal, "_calendar", lambda: None)
    monkeypatch.setenv("MARKET_HOLIDAYS", "")
    marketcal.extra_holidays.cache_clear()

    now = dt.datetime(2026, 11, 6, jam, menit, tzinfo=marketcal.WIB)
    assert marketcal.market_open(now) is harus_buka


def test_coerce_date_berbagai_bentuk():
    d = dt.date(2026, 11, 6)
    assert marketcal._coerce_date(None) is None
    assert marketcal._coerce_date(d) == d
    assert marketcal._coerce_date(dt.datetime(2026, 11, 6, 10, 30)) == d
    assert marketcal._coerce_date("2026-11-06") == d
    assert marketcal._coerce_date(["bukan", None, "2026-11-06"]) == d
    assert marketcal._coerce_date("bukan-tanggal") is None

"""Tes offline P16: zona beli (support/RSI/breakout) + kirim sekali saat masuk."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

import bot
from core import alerts, store


def _ind(**last) -> pd.DataFrame:
    """30 baris indikator netral; baris terakhir & sebelumnya bisa diatur."""
    base = {"close": 1000.0, "low": 990.0, "high": 1010.0, "volume": 1_000.0,
            "vol_avg20": 1_000.0, "rsi14": 50.0, "low20": 950.0,
            "high20": 1100.0, "atr14": 20.0, "ret1d": 0.0}
    rows = [dict(base) for _ in range(30)]
    rows[-2].update({k: v for k, v in last.items() if k.endswith("_prev")})
    for k, v in last.items():
        if not k.endswith("_prev"):
            rows[-1][k] = v
    df = pd.DataFrame(rows)
    df["prev_close"] = [np.nan] + list(df["close"][:-1])
    return df


def test_zona_support_saat_pantulan():
    df = _ind(low=952.0, close=960.0)
    df.loc[28, "close"] = 945.0            # kemarin lebih rendah
    assert "support" in alerts.buy_zones(df)


def test_zona_support_tidak_aktif_kalau_masih_jatuh():
    df = _ind(low=952.0, close=940.0)      # menyentuh support tapi close turun
    df.loc[28, "close"] = 955.0
    assert "support" not in alerts.buy_zones(df)


def test_zona_rsi_pulih_dari_oversold():
    df = _ind(rsi14=34.0)
    df.loc[28, "rsi14"] = 25.0
    assert "rsi" in alerts.buy_zones(df)
    df2 = _ind(rsi14=28.0)
    df2.loc[28, "rsi14"] = 20.0            # belum tembus 30
    assert "rsi" not in alerts.buy_zones(df2)


def test_zona_breakout_butuh_volume():
    df = _ind(close=1120.0, volume=2_000.0)
    assert "breakout" in alerts.buy_zones(df)
    tipis = _ind(close=1120.0, volume=1_100.0)   # < 1,5x rata-rata
    assert "breakout" not in alerts.buy_zones(tipis)


def test_tidak_ada_zona_saat_biasa():
    assert alerts.buy_zones(_ind()) == []


def test_pesan_zona_menyebut_level_dan_peringatan():
    pesan = alerts.build_zone_message(
        "BBCA", ["support", "rsi"], {"close": 1000.0, "ret1d": 1.2},
        sl=980.0, tp=1040.0)
    assert "MASUK ZONA BELI" in pesan
    assert "support" in pesan.lower() or "Memantul" in pesan
    assert "SL Rp980" in pesan and "TP Rp1.040" in pesan
    assert "bukan instruksi beli" in pesan


# --------------------------------------------------------------- kirim sekali
class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _App:
    def __init__(self):
        self.bot = _Bot()


def _jalan(monkeypatch, tmp_store, frames, auto=True):
    """Tidak dipakai; harness ada di masing-masing tes."""
    return None


@pytest.fixture
def wl(tmp_store):
    store.watchlist_add(100, "BBCA")
    return ["BBCA"]


def test_kirim_hanya_saat_masuk_zona(monkeypatch, tmp_store, wl):
    zona = _ind(low=952.0, close=960.0)
    zona.loc[28, "close"] = 945.0
    netral = _ind()
    frames = [zona, zona, netral, zona]
    monkeypatch.setattr(bot.analysis, "compute_indicators",
                        lambda df: frames.pop(0))
    monkeypatch.setattr(bot.analysis, "last_snapshot",
                        lambda ind: {"close": 960.0, "ret1d": 1.0})
    monkeypatch.setattr(bot.saranmod, "stop_price", lambda e, a: 940.0)
    monkeypatch.setattr(bot.saranmod, "target_price", lambda e, a: 1000.0)
    store.set_auto(100, True)
    app = _App()

    def bars(symbol):
        return pd.DataFrame({"close": [1.0]})

    asyncio.run(bot.check_zone_alerts(app, bars))
    assert len(app.bot.sent) == 1            # masuk zona -> 1 pesan
    asyncio.run(bot.check_zone_alerts(app, bars))
    assert len(app.bot.sent) == 1            # masih di zona -> diam
    asyncio.run(bot.check_zone_alerts(app, bars))
    assert len(app.bot.sent) == 1            # keluar zona -> diam
    asyncio.run(bot.check_zone_alerts(app, bars))
    assert len(app.bot.sent) == 2            # masuk lagi -> 1 pesan baru


def test_auto_off_menghentikan_zona(monkeypatch, tmp_store, wl):
    zona = _ind(low=952.0, close=960.0)
    zona.loc[28, "close"] = 945.0
    frames = [zona]
    monkeypatch.setattr(bot.analysis, "compute_indicators",
                        lambda df: frames.pop(0))
    store.set_auto(100, False)
    app = _App()
    asyncio.run(bot.check_zone_alerts(app, lambda s: pd.DataFrame({"c": [1]})))
    assert app.bot.sent == []

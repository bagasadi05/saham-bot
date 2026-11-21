"""Tes offline pelacak tesis posisi (`core/tesis.py`), store, `/portofolio`, dan alert exit."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime

import numpy as np
import pytest

import bot
from core import marketcal, store, tesis
from handlers import jobs as jobs_handler
from tests.conftest import make_prices


@pytest.fixture(autouse=True)
def _reset(tmp_store):
    bot._last_used.clear()
    bot.PENDING.clear()
    bot.ALLOWED_IDS.clear()


class _FakeMsg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self


class _FakeChat:
    def __init__(self, cid: int = 100):
        self.id = cid

    async def send_action(self, _action):
        return None


class _FakeUpdate:
    def __init__(self, text: str = "/portofolio", cid: int = 100):
        self.effective_chat = _FakeChat(cid)
        self.effective_user = types.SimpleNamespace(id=cid)
        self.effective_message = _FakeMsg(text)


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kw):
        self.sent.append((chat_id, text, kw))


# ----------------------------------------------------------- core/tesis.py

def test_evaluasi_empat_status_dan_tidak_tersedia(uptrend_df, downtrend_df):
    # 1) TIDAK_TERSEDIA bila harga kosong (jujur, tidak dikarang)
    na = tesis.evaluate_position("BBCA", 9000.0, None)
    assert na["status"] == tesis.TIDAK_TERSEDIA
    assert na["data_ok"] is False
    assert "tidak tersedia" in tesis.format_portfolio_line(na).lower()

    # 2) GUGUR saat SL tertembus (close atau intraday low)
    gugur_sl = tesis.evaluate_position(
        "BBCA", 9000.0, 8900.0, low=8750.0, sl=8800.0, df=downtrend_df,
    )
    assert gugur_sl["status"] == tesis.GUGUR
    assert gugur_sl["data_ok"] is True
    assert any("SL" in r for r in gugur_sl["reasons"])

    # 2b) GUGUR saat Chandelier trailing tembus
    gugur_ch = tesis.evaluate_position(
        "BBCA", 9000.0, 9100.0, trailing_sl=9200.0, trailing_hit=True,
    )
    assert gugur_ch["status"] == tesis.GUGUR

    # 2c) GUGUR saat forensik berat + tren negatif
    gugur_for = tesis.evaluate_position(
        "XYZ", 1000.0, 970.0, tech_score={"pct": -15},
        fund={"pct": -30, "forensic_penalty": 30},
    )
    assert gugur_for["status"] == tesis.GUGUR

    # 3) MELEMAH saat peringkat relatif jatuh di bawah pita tahan 60% & tren lemah
    melemah = tesis.evaluate_position(
        "TLKM", 3200.0, 3050.0, sl=2800.0,
        tech_score={"pct": -25}, rel_pct=42.0,
        news_sent={"pct": -35.0, "n": 4},
        market_regime={"status": "DEFENSIVE"},
    )
    assert melemah["status"] == tesis.MELEMAH
    assert melemah["score"] <= -2

    # 4) MENGUAT saat tren kuat + untung berjalan + peringkat atas
    menguat = tesis.evaluate_position(
        "BMRI", 5000.0, 5600.0, sl=4700.0, tp=5500.0,
        df=uptrend_df, rel_pct=85.0,
        fund={"pct": 35.0, "forensic_penalty": 0},
        news_sent={"pct": 40.0, "n": 3},
    )
    assert menguat["status"] == tesis.MENGUAT
    assert menguat["score"] >= 2

    # 5) TETAP saat datar tanpa pemicu dominan
    tetap = tesis.evaluate_position(
        "ASII", 5000.0, 5025.0, sl=4700.0,
        tech_score={"pct": 5}, rel_pct=65.0,
    )
    assert tetap["status"] == tesis.TETAP


def test_transisi_status_hanya_alert_saat_berubah():
    # Pertama kali dipantau: sehat -> diam; langsung melemah/gugur -> alert
    assert tesis.evaluate_transition(None, tesis.TETAP)["should_alert"] is False
    assert tesis.evaluate_transition(None, tesis.MENGUAT)["should_alert"] is False
    assert tesis.evaluate_transition(None, tesis.MELEMAH)["should_alert"] is True
    assert tesis.evaluate_transition(None, tesis.GUGUR)["should_alert"] is True

    # Status tidak berubah -> TIDAK alert
    for st in tesis.VALID_STATUSES:
        tr = tesis.evaluate_transition(st, st)
        assert tr["changed"] is False and tr["should_alert"] is False

    # Status memburuk -> alert
    t1 = tesis.evaluate_transition(tesis.TETAP, tesis.MELEMAH)
    assert t1["changed"] is True and t1["should_alert"] is True and t1["direction"] == "memburuk"
    t2 = tesis.evaluate_transition(tesis.MELEMAH, tesis.GUGUR)
    assert t2["changed"] is True and t2["should_alert"] is True and t2["direction"] == "memburuk"

    # Pulih dari MELEMAH/GUGUR -> alert pemulihan
    t3 = tesis.evaluate_transition(tesis.MELEMAH, tesis.MENGUAT)
    assert t3["changed"] is True and t3["should_alert"] is True and t3["direction"] == "membaik"

    # Pergeseran sehat TETAP <-> MENGUAT -> berubah tetapi tidak mengganggu alert exit
    t4 = tesis.evaluate_transition(tesis.TETAP, tesis.MENGUAT)
    assert t4["changed"] is True and t4["should_alert"] is False

    # Data tidak tersedia -> tidak pernah alert
    t5 = tesis.evaluate_transition(tesis.TETAP, tesis.TIDAK_TERSEDIA)
    assert t5["changed"] is False and t5["should_alert"] is False


# ----------------------------------------------------------- store persistence

def test_store_thesis_roundtrip_isolasi_dan_hapus_saat_tutup():
    store.set_user_mode(100, "expert")
    store.add_real_position(100, "BBCA", 5, 9000.0, "2026-11-01", "Financials")
    store.set_position_thesis(100, "BBCA", status=tesis.TETAP, score=1, reasons=["stabil"])

    got = store.get_position_thesis(100, "BBCA")
    assert got is not None and got["status"] == tesis.TETAP and got["score"] == 1
    assert "BBCA" in store.get_all_position_theses(100)
    assert store.get_position_thesis(200, "BBCA") is None

    # Jual sebagian -> tesis tetap tersimpan
    store.reduce_real_position(100, "BBCA", 2, 9200.0, "2026-11-05")
    assert store.get_position_thesis(100, "BBCA") is not None

    # Jual habis (lots_left == 0) -> tesis ikut dibersihkan otomatis tanpa mengganggu user_mode
    store.reduce_real_position(100, "BBCA", 3, 9300.0, "2026-11-10")
    assert store.get_position_thesis(100, "BBCA") is None
    assert store.get_user_mode(100) == "expert"

    # JSON korup aman tidak crash
    with store._db() as conn:
        store._ensure_chat(conn, 100)
        conn.execute(
            "INSERT OR REPLACE INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?)",
            ("100", "thesis_state:TLKM", "{korup"),
        )
    assert store.get_position_thesis(100, "TLKM") is None
    store.clear_position_thesis(100, "TLKM")


# ----------------------------------------------------------- /portofolio integration

def test_portofolio_menampilkan_status_tesis_dan_perubahan(monkeypatch, uptrend_df):
    store.add_real_position(100, "BBCA", 4, 2500.0, "2026-11-01", "Financials")
    # Simulasikan sebelumnya berstatus MELEMAH agar perubahan (dari MELEMAH) tampil
    store.set_position_thesis(100, "BBCA", status=tesis.MELEMAH, score=-2, reasons=["lama"])
    store.save_market_scan(
        [{"ticker": "BBCA", "signal": "BELI", "rel_pct": 85.0, "price": 3000.0, "trade_date": "2026-11-20"}],
        {"trade_date": "2026-11-20"},
    )
    monkeypatch.setattr(bot, "_quote_posisi", lambda _t: {
        "close": float(uptrend_df["close"].iloc[-1]),
        "prev_close": float(uptrend_df["close"].iloc[-2]),
        "atr": 40.0,
        "stale": False,
        "as_of": "2026-11-20",
        "df": uptrend_df,
        "rets": uptrend_df["close"].pct_change().dropna(),
    })
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: None)

    upd = _FakeUpdate("/portofolio", cid=100)
    asyncio.run(bot.cmd_portofolio(upd, types.SimpleNamespace()))
    assert upd.effective_message.replies
    txt = upd.effective_message.replies[-1][0]
    assert "Tesis:" in txt
    assert "MENGUAT" in txt
    assert "(dari MELEMAH)" in txt
    # Tersimpan status terbaru
    assert store.get_position_thesis(100, "BBCA")["status"] == tesis.MENGUAT


# ----------------------------------------------------------- exit alert transition-only

def test_exit_alert_tesis_hanya_kirim_saat_status_berubah_lintas_hari(monkeypatch):
    # Mulai posisi dengan harga 9000, SL sangat longgar (7000) supaya tidak memicu sl_hit
    store.record_execution_buy(
        100, "TLKM", 5, 9000.0, "2026-11-18",
        signal_price=9000.0, sl=7000.0, tp=11000.0,
        source="rencana", sector="Infrastructure",
    )
    # Seed tesis awal = TETAP
    store.set_position_thesis(100, "TLKM", status=tesis.TETAP, score=0, reasons=["awal"])
    # Snapshot pasar menempatkan TLKM di persentil 40% (< pita tahan 60%) -> MELEMAH
    store.save_market_scan(
        [{"ticker": "TLKM", "signal": "TAHAN", "rel_pct": 40.0, "price": 8600.0, "trade_date": "2026-11-19"}],
        {"trade_date": "2026-11-19"},
    )

    # Harga melemah ringan (di atas Chandelier trailing stop), sementara rel_pct < 60% -> MELEMAH
    weak_df = make_prices(np.linspace(9050, 8880, 60), start="2026-08-20")
    monkeypatch.setattr(bot.marketcal, "market_open", lambda: True)
    monkeypatch.setattr(
        bot.marketcal, "now_wib",
        lambda: datetime(2026, 11, 19, 10, 0, tzinfo=marketcal.WIB),
    )
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: weak_df)

    # Hari 1, Putaran 1: TETAP -> MELEMAH => 1 alert dikirim!
    app = types.SimpleNamespace(bot=_FakeBot())
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1
    assert "Tesis Posisi MELEMAH" in app.bot.sent[0][1]
    assert "(sebelumnya TETAP)" in app.bot.sent[0][1]
    assert store.get_position_thesis(100, "TLKM")["status"] == tesis.MELEMAH

    # Hari 2 (hari berbeda!), kondisi masih MELEMAH -> TIDAK kirim ulang!
    monkeypatch.setattr(
        bot.marketcal, "now_wib",
        lambda: datetime(2026, 11, 20, 10, 0, tzinfo=marketcal.WIB),
    )
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1

    # Hari 2 sore: harga jebol SL 7000 -> MELEMAH -> GUGUR => kirim 1 alert baru!
    broken_df = make_prices(
        np.concatenate([np.linspace(9000, 8600, 58), np.array([7200.0, 6900.0])]),
        start="2026-08-20",
    )
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: broken_df)
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 2
    assert "Tesis Posisi GUGUR" in app.bot.sent[1][1]
    assert "(sebelumnya MELEMAH)" in app.bot.sent[1][1]
    assert store.get_position_thesis(100, "TLKM")["status"] == tesis.GUGUR


def test_exit_alert_gagal_kirim_tidak_menghilangkan_alert_berikutnya(monkeypatch):
    store.record_execution_buy(
        100, "TLKM", 5, 9000.0, "2026-11-18",
        signal_price=9000.0, sl=7000.0, tp=11000.0,
        source="rencana", sector="Infrastructure",
    )
    store.set_position_thesis(100, "TLKM", status=tesis.TETAP, score=0, reasons=["awal"])
    store.save_market_scan(
        [{"ticker": "TLKM", "signal": "TAHAN", "rel_pct": 40.0, "price": 8600.0, "trade_date": "2026-11-19"}],
        {"trade_date": "2026-11-19"},
    )
    weak_df = make_prices(np.linspace(9050, 8880, 60), start="2026-08-20")
    monkeypatch.setattr(bot.marketcal, "market_open", lambda: True)
    monkeypatch.setattr(
        bot.marketcal, "now_wib",
        lambda: datetime(2026, 11, 19, 10, 0, tzinfo=marketcal.WIB),
    )
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: weak_df)

    class _FlakyBot(_FakeBot):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        async def send_message(self, chat_id: int, text: str, **kw):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("Telegram timeout")
            await super().send_message(chat_id, text, **kw)

    flaky = _FlakyBot()
    app = types.SimpleNamespace(bot=flaky)

    # Putaran 1 gagal kirim -> alerted_status tetap TETAP (alert tidak hilang!)
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(flaky.sent) == 0
    assert store.get_position_thesis(100, "TLKM")["alerted_status"] == tesis.TETAP

    # Putaran 2 berhasil -> alert MELEMAH akhirnya terkirim & alerted_status diperbarui
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(flaky.sent) == 1
    assert "Tesis Posisi MELEMAH" in flaky.sent[0][1]
    assert store.get_position_thesis(100, "TLKM")["alerted_status"] == tesis.MELEMAH


def test_portofolio_selaras_sl_avg_dan_tidak_membungkam_atau_pingpong_alert(monkeypatch):
    # Posisi tanpa SL eksekusi (avg=9000), harga turun ke 8400 (tembus avg - 1*ATR)
    store.add_real_position(100, "BBCA", 5, 9000.0, "2026-11-18", "Financials")
    store.set_position_thesis(100, "BBCA", status=tesis.TETAP, score=0, reasons=["awal"])

    drop_df = make_prices(
        np.concatenate([np.linspace(9050, 8950, 58), np.array([8600.0, 8400.0])]),
        start="2026-08-20",
    )
    monkeypatch.setattr(bot, "_quote_posisi", lambda _t: {
        "close": float(drop_df["close"].iloc[-1]),
        "prev_close": float(drop_df["close"].iloc[-2]),
        "atr": 150.0,
        "stale": False,
        "as_of": "2026-11-19",
        "df": drop_df,
        "rets": drop_df["close"].pct_change().dropna(),
    })
    monkeypatch.setattr(bot.marketcal, "market_open", lambda: True)
    monkeypatch.setattr(
        bot.marketcal, "now_wib",
        lambda: datetime(2026, 11, 19, 10, 0, tzinfo=marketcal.WIB),
    )
    monkeypatch.setattr(
        bot.data, "fetch_history",
        lambda ticker, *a, **k: None if str(ticker) == "^JKSE" else drop_df,
    )

    # 1) User buka /portofolio lebih dulu -> tampil GUGUR (SL berbasis avg_price),
    #    namun alerted_status tetap TETAP agar scan_alerts tetap mengirim notifikasi.
    upd = _FakeUpdate("/portofolio", cid=100)
    asyncio.run(bot.cmd_portofolio(upd, types.SimpleNamespace()))
    txt = upd.effective_message.replies[-1][0]
    assert "GUGUR" in txt and "(dari TETAP)" in txt
    st_after_porto = store.get_position_thesis(100, "BBCA")
    assert st_after_porto["status"] == tesis.GUGUR
    assert st_after_porto["alerted_status"] == tesis.TETAP

    # 2) scan_alerts berjalan -> tetap mengirim alert GUGUR sekali, lalu alerted_status = GUGUR
    app = types.SimpleNamespace(bot=_FakeBot())
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1
    assert "Tesis Posisi GUGUR" in app.bot.sent[0][1]
    assert store.get_position_thesis(100, "BBCA")["alerted_status"] == tesis.GUGUR

    # 3) User buka /portofolio lagi + scan_alerts lagi -> TIDAK ping-pong spam!
    bot._last_used.clear()
    asyncio.run(bot.cmd_portofolio(_FakeUpdate("/portofolio", cid=100), types.SimpleNamespace()))
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1


def test_reason_ma50_jujur_saat_sma20_kosong():
    import pandas as pd

    ind = pd.DataFrame({"close": [9200.0], "sma50": [9000.0]})
    res = tesis.evaluate_position("BBCA", 9000.0, 9200.0, ind=ind, tech_score={"pct": 30})
    assert any("di atas MA50" in r and "MA20" not in r for r in res["reasons"])

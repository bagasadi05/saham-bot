"""Tes offline P17: tabel portofolio asli + ringkasan + handler."""
from __future__ import annotations

import asyncio
import types

import pytest

import bot
from core import portofolio, store


# ------------------------------------------------------------------- store
def test_beli_dua_kali_harga_rata_rata_ditimbang(tmp_store):
    store.add_real_position(100, "bbca", 5, 9000, "2026-11-10", "Financials")
    res = store.add_real_position(100, "BBCA", 5, 10_000, "2026-11-11")
    assert res["lots"] == 10
    assert res["avg_price"] == pytest.approx(9500.0)
    pos = store.real_positions(100)
    assert len(pos) == 1 and pos[0]["sector"] == "Financials"


def test_jual_sebagian_menghitung_realisasi(tmp_store):
    store.add_real_position(100, "BBCA", 5, 9000, "2026-11-10")
    store.add_real_position(100, "BBCA", 5, 10_000, "2026-11-11")
    res = store.reduce_real_position(100, "BBCA", 3, 10_000, "2026-11-12")
    assert res["lots_left"] == 7
    assert res["realized"] == 150_000          # (10.000-9.500) x 300 lembar
    assert store.real_positions(100)[0]["lots"] == 7


def test_jual_semua_menghapus_posisi(tmp_store):
    store.add_real_position(100, "TLKM", 2, 3000, "2026-11-10")
    res = store.reduce_real_position(100, "TLKM", 2, 2800, "2026-11-12")
    assert res["lots_left"] == 0
    assert res["realized"] == -40_000
    assert store.real_positions(100) == []


def test_jual_melebihi_kepemilikan_ditolak(tmp_store):
    store.add_real_position(100, "BBCA", 2, 9000, "2026-11-10")
    with pytest.raises(ValueError, match="hanya punya 2 lot"):
        store.reduce_real_position(100, "BBCA", 3, 9000, "2026-11-11")
    with pytest.raises(ValueError, match="tidak ada posisi"):
        store.reduce_real_position(100, "ANTM", 1, 1500, "2026-11-11")


def test_lot_atau_harga_tidak_masuk_akal_ditolak(tmp_store):
    with pytest.raises(ValueError):
        store.add_real_position(100, "BBCA", 0, 9000, "2026-11-10")
    with pytest.raises(ValueError):
        store.add_real_position(100, "BBCA", 1, 0, "2026-11-10")


def test_riwayat_transaksi_urut_terbaru(tmp_store):
    store.add_real_position(100, "BBCA", 1, 9000, "2026-11-10")
    store.reduce_real_position(100, "BBCA", 1, 9500, "2026-11-11")
    trades = store.real_trades(100)
    assert [t["side"] for t in trades] == ["JUAL", "BELI"]


def test_skema_terkini(tmp_store):
    store.counts()          # memicu _ensure_ready
    import sqlite3
    conn = sqlite3.connect(store.STORE_DB)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert ver == store.SCHEMA_VERSION
    assert {"real_positions", "real_trades"} <= tables


def test_db_lama_v2_naik_ke_versi_terkini(tmp_store):
    """DB produksi yang sudah v2 harus naik tanpa kehilangan data."""
    import sqlite3
    conn = sqlite3.connect(store.STORE_DB)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    store.add_real_position(100, "BBCA", 1, 9000, "2026-11-12")

    conn = sqlite3.connect(store.STORE_DB)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert ver == store.SCHEMA_VERSION
    assert store.real_positions(100)[0]["lots"] == 1


# --------------------------------------------------------------- ringkasan
def _pos(ticker, lots, avg, sector):
    return {"ticker": ticker, "lots": lots, "avg_price": avg, "sector": sector}


def test_summarize_pnl_bobot_dan_risiko():
    # STOP_ATR = 1.0 selaras dengan saran.stop_price() (1x ATR, tick BEI):
    # BBCA 9500 - 250 = 9250 (risk 500 lbr x 250 = 125.000),
    # TLKM 2800 - 100 = 2700 (risk 200 lbr x 100 = 20.000) -> total 145.000 (2,7%).
    s = portofolio.summarize(
        [_pos("BBCA", 5, 9000, "Financials"), _pos("TLKM", 2, 3000, "Infra")],
        {"BBCA": {"close": 9500, "atr": 250},
         "TLKM": {"close": 2800, "atr": 100}})
    bbca = next(r for r in s["rows"] if r["ticker"] == "BBCA")
    assert bbca["pnl"] == 250_000
    assert bbca["sl"] == 9250.0            # 9500 - 1x250 (tick 25)
    assert bbca["sl_source"] == "atr"
    assert bbca["risk"] == 125_000         # 500 lembar x 250
    assert s["value"] == 5_310_000
    assert s["cost"] == 5_100_000
    assert s["pnl"] == 210_000
    assert s["risk"] == 145_000
    assert s["risk_pct"] == pytest.approx(2.7, abs=0.1)
    assert bbca["weight_pct"] > 89


def test_summarize_konsentrasi_dan_sektor():
    s = portofolio.summarize(
        [_pos("BBCA", 10, 9000, "Financials"), _pos("TLKM", 1, 3000, "Infra")],
        {"BBCA": {"close": 9000, "atr": 200},
         "TLKM": {"close": 3000, "atr": 100}})
    assert any("Financials" in x for x in s["warnings"])
    assert s["sectors"][0]["sector"] == "Financials"


def test_summarize_posisi_tanpa_harga_tidak_dianggap_nol():
    s = portofolio.summarize(
        [_pos("BBCA", 5, 9000, "Financials"), _pos("XXXX", 1, 1000, None)],
        {"BBCA": {"close": 9000, "atr": 200}, "XXXX": {"close": None}})
    assert s["unpriced"] == 1
    assert s["value"] == 4_500_000        # hanya yang terbaca
    assert s["rows"][1]["priced"] is False


# ----------------------------------------------------------------- handler
class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self


class _Chat:
    id = 100

    async def send_action(self, *a, **k):
        return None


class _User:
    id = 100


class _Update:
    def __init__(self, text):
        self.effective_message = _Msg(text)
        self.effective_chat = _Chat()
        self.effective_user = _User()


@pytest.fixture
def jalankan(monkeypatch, tmp_store):
    from core import fundamental
    monkeypatch.setattr(fundamental, "fetch_fundamentals",
                        lambda t: {"sector": "Financials"})
    monkeypatch.setattr(bot, "_quote_posisi",
                        lambda t: {"close": 9500.0, "atr": 250.0,
                                   "stale": False})
    bot._last_used.clear()

    def run(text):
        upd = _Update(text)
        asyncio.run(bot.cmd_portofolio(upd, types.SimpleNamespace()))
        return upd.effective_message.replies

    return run


def _teks(replies):
    return "\n".join(replies)


def test_handler_beli_lalu_lihat(jalankan):
    teks = _teks(jalankan("/portofolio beli BBCA 5 9000"))
    assert "Tercatat" in teks and "BELI" in teks and "5 lot" in teks
    teks = _teks(jalankan("/portofolio"))
    assert "PORTOFOLIO ASLI" in teks
    assert "BBCA" in teks and "Rp4.750.000" in teks
    assert "Risiko total" in teks
    assert "Risiko portofolio" in teks and "eksposur" in teks


def test_handler_jual_dan_riwayat(jalankan):
    jalankan("/portofolio beli BBCA 5 9000")
    teks = _teks(jalankan("/portofolio jual BBCA 2 9500"))
    assert "Realisasi" in teks and "Sisa 3 lot" in teks
    teks = _teks(jalankan("/portofolio riwayat"))
    assert "JUAL" in teks and "BELI" in teks


def test_handler_jual_melebihi_kepemilikan_ramah(jalankan):
    jalankan("/portofolio beli BBCA 1 9000")
    teks = _teks(jalankan("/portofolio jual BBCA 5 9500"))
    assert "hanya punya 1 lot" in teks


def test_handler_tanpa_posisi_memberi_contoh(jalankan):
    teks = _teks(jalankan("/portofolio"))
    assert "Belum ada posisi" in teks and "/portofolio beli" in teks


def test_handler_input_kurang_ditolak(jalankan):
    teks = _teks(jalankan("/portofolio beli BBCA"))
    assert "Portofolio asli" in teks and "beli BBCA 5 9000" in teks

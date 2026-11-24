"""Tes regresi untuk seluruh 8 kelompok bug kritis P0 (A1-A8) dari audit_sahambot.md."""
from __future__ import annotations

import asyncio
import datetime as dt
import re

from core import (
    cek, fundamental, jurnal, kualitas, learn, portofolio, pricing, saran, textutil, verdict,
)


def test_a1_send_chart_file_tetap_terbuka_saat_await(tmp_path, monkeypatch):
    # Simulasikan reply_photo asli yang membaca stream foto secara async
    chart_file = tmp_path / "chart.png"
    chart_file.write_bytes(b"PNG_DATA")
    read_bytes = []

    class _StrictMsg:
        async def reply_photo(self, photo, **_kw):
            assert not photo.closed, "File handle sudah tertutup sebelum reply_photo di-await!"
            read_bytes.append(photo.read())

    async def _send_with_retry(fn, **_kw):
        return await fn()

    async def _send_chart(_path=str(chart_file)):
        with open(_path, "rb") as f:
            return await _StrictMsg().reply_photo(photo=f)

    asyncio.run(_send_with_retry(_send_chart))
    assert read_bytes == [b"PNG_DATA"]


def test_a2_tick_lintas_pita_ara_dan_akselerasi():
    # Entry 500 (tick=5), ATR 33 -> SL 467 di pita <500 wajib kelipatan 2 (bukan 465!)
    sl = saran.stop_price(500.0, 33.0)
    assert sl % 2 == 0 and sl < 500.0
    # Entry 4900 (tick=10), ATR 120 -> TP 5140 di pita >=5000 wajib kelipatan 25!
    tp = saran.target_price(4900.0, 120.0)
    assert tp % 25 == 0 and tp > 5000.0
    # ARA basis prev_close=180 (tier <=200 -> +35% = 242): harga 226 (+25.6%) BELUM ARA!
    assert pricing.is_ara_locked(226.0, 180.0, dt.date(2026, 11, 20)) is False
    # ARA basis prev_close=5050 (+20% = floor_tick(6060) = 6050): harga 6050 SUDAH ARA!
    assert pricing.is_ara_locked(6050.0, 5050.0, dt.date(2026, 11, 20)) is True
    # Papan Akselerasi = 10%
    assert pricing.arb_pct(100.0, dt.date(2026, 11, 20), board="Akselerasi") == 10.0


def test_a3_portofolio_unpriced_tidak_rugi_100_persen():
    positions = [
        {"ticker": "BBCA", "lots": 10, "avg_price": 1000.0, "sector": "Financials"},
        {"ticker": "FAIL", "lots": 10, "avg_price": 1000.0, "sector": "Energy"},
    ]
    quotes = {
        "BBCA": {"close": 1100.0},
        "FAIL": {"close": None},
    }
    res = portofolio.summarize(positions, quotes)
    # BBCA untung +100rb; FAIL unpriced TIDAK boleh dihitung rugi -1jt!
    assert res["pnl"] == 100_000
    assert res["pnl_pct"] == 10.0


def test_a4_verdict_forensic_penalty_positif_memblokir_tahan():
    title, body = verdict._verdict_jual(
        ts_pct=65.0,
        close=1000.0,
        support=950.0,
        forensic_penalty=40.0,
        forensic_flags=["OCF negatif"],
    )
    assert title != "🟢 TAHAN"
    assert "-40 poin" in body
    assert "+40 poin" not in body


def test_a5_fcf_yield_tolak_beda_mata_uang_usd_vs_idr():
    f_usd = {"currency": "USD", "price_currency": "IDR", "fcf": 100_000_000.0, "market_cap": 50_000_000_000_000.0}
    assert fundamental.compute_fcf_yield(f_usd) is None
    f_idr = {"currency": "IDR", "price_currency": "IDR", "fcf": 1_000_000_000_000.0, "market_cap": 20_000_000_000_000.0}
    assert fundamental.compute_fcf_yield(f_idr) == 5.0


def test_a6_kualitas_tolak_ekuitas_negatif_dan_cek_num_desimal():
    q = kualitas.judge({"per": None, "pbv": -2.0, "roe": None})
    assert q["ok"] is False
    assert "ekuitas" in q["label"].lower()
    assert cek._num("12.34") == 12.34
    assert cek._num("1.234.567") == 1234567.0


def test_a7_semua_topik_belajar_html_valid():
    bad_lt = re.compile(r"<(?!(?:/?(?:b|i|code)>))")
    for key, (title, body) in learn.TOPICS.items():
        assert not bad_lt.search(title), f"Raw < di judul topik {key}"
        assert not bad_lt.search(body), f"Raw < di isi topik {key}"


def test_a8_parse_exec_lembar_dan_parse_rupiah():
    assert jurnal.parse_exec_input("500 lembar @ 9050") == (5, 9050.0)
    assert jurnal.parse_exec_input("10 lot @ 9050") == (10, 9050.0)
    assert textutil.parse_rupiah("1,500jt") == 1_500_000.0
    assert textutil.parse_rupiah("5,000.50") == 5000.5

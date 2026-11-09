"""Test pembobotan PowerScore (50% teknikal, 30% fundamental, 20% berita)."""
from __future__ import annotations

from core import powerscore


def test_bobot_sesuai_konstanta():
    assert (powerscore.W_TECH, powerscore.W_FUND, powerscore.W_NEWS) == (0.5, 0.3, 0.2)


def test_kombinasi_hanya_teknikal():
    # Komponen hilang tidak dianggap 0 — bobot dinormalisasi ke komponen yang ada
    assert powerscore.combine(100, None, None)["pct"] == 100
    assert powerscore.combine(-100, None, None)["pct"] == -100
    assert powerscore.combine(0, None, None)["rating"] == "NETRAL"


def test_kombinasi_tiga_pin():
    # 50*0.5 + 100*0.3 + (-100)*0.2 = 25 + 30 - 20 = 35
    assert powerscore.combine(50, 100, -100)["pct"] == 35


def test_hasil_dibatasi_minus100_sampai_100():
    assert powerscore.combine(100, 100, 100)["pct"] == 100
    assert powerscore.combine(-100, -100, -100)["pct"] == -100


def test_rating_mengikuti_ambang():
    assert powerscore.combine(100, 100, 100)["rating"] == "STRONG BUY"
    assert powerscore.combine(40, 40, 40)["rating"] == "BUY"
    assert powerscore.combine(0, 0, 0)["rating"] == "NETRAL"
    assert powerscore.combine(-40, -40, -40)["rating"] == "SELL"
    assert powerscore.combine(-100, -100, -100)["rating"] == "STRONG SELL"


def test_trap_warning_hanya_saat_momentum_ekstrem():
    assert powerscore.trap_warning(20, 1) is not None
    assert powerscore.trap_warning(10, 1) is None
    assert powerscore.trap_warning(20, -1) is None
    assert powerscore.trap_warning(None, 1) is None


def test_judge_memuat_ketiga_pin():
    teks = powerscore.judge(
        10,
        {"pct": 50},
        {"pct": -20},
        {"ret20d": 5, "ret1d": 1},
    )
    assert "POWERSCORE" in teks
    assert "Teknikal" in teks and "Fundamental" in teks and "Berita" in teks

"""Peringatan kuintil tidak monoton di /validasi — dua cabang + tepi.

Cabang: (1) tak monoton → baris peringatan muncul dengan alasan spesifik;
(2) monoton → tanpa baris. Data parsial/non-numerik tidak dinilai.
"""
from __future__ import annotations

import bot


def _payload(**kw):
    p = {
        "meta": {"start": "2017-09-07", "end": "2026-10-09",
                 "n_tickers": 45, "n_rows": 91_395, "horizon": 20,
                 "universe": "LQ45_2026"},
        "score": {"ic": {"mean_ic": 0.0115, "t": 2.68, "t_hac": 0.85},
                  "ic_timeseries": {"mean_ic": -0.0207, "t": -2.33}},
        "ratings": [], "walk_forward": [],
        "quintiles": [], "portfolio": None,
    }
    p.update(kw)
    return p


def _kuintil(e1, e2, e3, e4, e5):
    return [{"quintile": i + 1, "excess_pct": e}
            for i, e in enumerate((e1, e2, e3, e4, e5))]


def test_kuintil_tak_monoton_q2_di_bawah_q1_menampilkan_peringatan():
    # Bentuk data/validation.json saat ini: Q2 (-0.391) lebih buruk dari Q1 (-0.204).
    txt = bot.format_validasi(_payload(
        quintiles=_kuintil(-0.204, -0.391, -0.100, 0.292, 0.400)))
    assert "⚠️ Kalibrasi kuintil belum monoton (Q2 < Q1)" in txt
    assert "ekspektasi return per kuintil belum bisa dipercaya" in txt
    assert "pakai peringkat relatifnya saja" in txt
    assert "Q1 -0.20%" in txt  # nilai kuintil tetap ditampilkan apa adanya


def test_kuintil_tak_monoton_q5_tidak_di_atas_q3():
    txt = bot.format_validasi(_payload(
        quintiles=_kuintil(-0.500, -0.200, 0.100, 0.300, 0.050)))
    assert "belum monoton (Q5 ≤ Q3)" in txt
    assert "Q2 < Q1" not in txt


def test_kuintil_tak_monoton_q3_tidak_di_atas_q1():
    txt = bot.format_validasi(_payload(
        quintiles=_kuintil(-0.100, 0.050, -0.300, 0.200, 0.500)))
    assert "belum monoton (Q3 ≤ Q1)" in txt


def test_kuintil_dua_pelanggaran_digabung():
    txt = bot.format_validasi(_payload(
        quintiles=_kuintil(-0.100, -0.300, -0.200, 0.100, 0.150)))
    assert "belum monoton (Q2 < Q1; Q3 ≤ Q1)" in txt


def test_kuintil_monoton_tanpa_peringatan():
    txt = bot.format_validasi(_payload(
        quintiles=_kuintil(-0.400, -0.100, 0.000, 0.200, 0.400)))
    assert "belum monoton" not in txt
    assert "Q5 +0.40%" in txt


def test_kuintil_parsial_tidak_dinilai():
    # Payload lama (test_bot_text) hanya Q1 & Q5 → tak bisa dinilai, jangan mengarang.
    txt = bot.format_validasi(_payload(
        quintiles=[{"quintile": 1, "excess_pct": -0.242},
                   {"quintile": 5, "excess_pct": 0.424}]))
    assert "belum monoton" not in txt


def test_helper_abaikan_nilai_non_numerik():
    # Catatan: baris tampilan format_validasi sudah lama crash pada excess
    # non-numerik (perilaku lama, di luar cakupan tugas ini); helper baru sendiri
    # toleran — nilai non-numerik dilewati, data tak lengkap tidak dinilai.
    q = [{"quintile": 1, "excess_pct": None},
         {"quintile": 2, "excess_pct": "x"},
         {"quintile": 3, "excess_pct": 0.0},
         {"quintile": 4, "excess_pct": 0.1},
         {"quintile": 5, "excess_pct": 0.2}]
    assert bot._kuintil_warning(q) is None


def test_helper_langsung_dua_cabang():
    assert bot._kuintil_warning(
        _kuintil(-0.2, -0.4, -0.1, 0.3, 0.4)) is not None
    assert bot._kuintil_warning(
        _kuintil(-0.4, -0.1, 0.0, 0.2, 0.4)) is None
    assert bot._kuintil_warning([]) is None

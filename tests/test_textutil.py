"""Test pemotongan pesan Telegram (core.textutil)."""
from __future__ import annotations

import pytest

from core import textutil


def _audit(parts, limit):
    for part in parts:
        assert len(part) <= limit, f"potongan {len(part)} > {limit}"
        assert part.count("<pre>") == part.count("</pre>"), part[:80]


def test_potongan_teks_biasa_utuh_dan_maksimal():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    parts = textutil.split_message(text, limit=500)

    _audit(parts, 500)
    assert len(parts) > 1
    assert "\n".join(parts) == text


def test_baris_ekstrem_dipotong_paksa_tanpa_kehilangan_teks():
    text = "y" * 1200
    parts = textutil.split_message(text, limit=500)

    _audit(parts, 500)
    assert "".join(parts) == text


def test_baris_panjang_di_dalam_pre_tetap_seimbang():
    # Regresi: dulu potongan bisa melebihi limit (tag dihitung ganda) dan
    # menyisakan "</pre>" nyasar sehingga Telegram menolak pesan.
    text = "<pre>" + "A" * 5000 + "\n" + "B" * 100 + "\n</pre>"
    parts = textutil.split_message(text, limit=1000)

    _audit(parts, 1000)
    assert "".join(parts).replace("<pre>", "").replace("</pre>", "") == (
        "A" * 5000 + "\n" + "B" * 100 + "\n")


def test_pre_dibuka_ulang_di_potongan_berikutnya():
    rows = "\n".join(f"EMTN{i:02d}  12.3  4.5  +10%" for i in range(60))
    text = f"Header\n<pre>{rows}</pre>\nFooter"
    parts = textutil.split_message(text, limit=250)

    _audit(parts, 250)
    assert len(parts) > 2
    # konten pre tidak boleh hilang dan tidak ada tag yang terbelah
    for part in parts:
        assert "<pr" not in part.replace("<pre>", "")
        assert "</pr" not in part.replace("</pre>", "")


def test_tag_tidak_terbelah_saat_mepet_batas():
    # "<pre>" pas di batas budget: potongan tidak boleh berakhir "<pr"
    text = "z" * 90 + "<pre>" + "k" * 400 + "</pre>"
    parts = textutil.split_message(text, limit=100)

    _audit(parts, 100)
    body = "".join(parts)
    assert body.replace("<pre>", "").replace("</pre>", "") == "z" * 90 + "k" * 400
    assert "<pr" not in body.replace("<pre>", "")
    assert "</pr" not in body.replace("</pre>", "")


def test_teks_pendek_tidak_dipotong():
    assert textutil.split_message("halo", limit=100) == ["halo"]


def test_limit_terlalu_kecil_ditolak():
    with pytest.raises(ValueError):
        textutil.split_message("x" * 50, limit=10)


def test_pre_tidak_tertutup_di_input_tetap_ditutup():
    parts = textutil.split_message("<pre>" + "\n" + "a" * 300, limit=100)

    _audit(parts, 100)

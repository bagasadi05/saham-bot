"""Tiket order limit: isi order + kapan dipasang/dibatalkan."""
from __future__ import annotations

from handlers import market


class _Deps:
    @staticmethod
    def _money(value) -> str:
        return f"Rp{value:,.0f}".replace(",", ".")


def _plan(**over):
    plan = {"entry": 3100.0, "sl": 2990.0, "tp": 3320.0,
            "chase_above": 3160.0, "last_price": 3090.0, "lots": 1}
    plan.update(over)
    return plan


def test_tiket_menampilkan_limit_lot_sl_tp():
    text = "\n".join(market._order_ticket(_plan(), _Deps))

    assert "Tiket order limit" in text
    assert "Limit <b>Rp3.100</b>" in text
    assert "<b>1 lot</b>" in text
    assert "SL <b>Rp2.990</b>" in text
    assert "TP <b>Rp3.320</b>" in text


def test_pasang_sekarang_bila_penutupan_di_bawah_limit():
    text = "\n".join(market._order_ticket(_plan(last_price=3090.0), _Deps))
    assert "bisa dipasang sekarang" in text


def test_tunggu_bila_penutupan_di_atas_limit():
    text = "\n".join(market._order_ticket(_plan(last_price=3120.0), _Deps))
    assert "tunggu harga turun" in text
    assert "jangan beli di atas <b>Rp3.160</b>" in text


def test_selalu_menyebut_batalkan_bila_tidak_tersentuh():
    text = "\n".join(market._order_ticket(_plan(), _Deps))
    assert "batalkan" in text


def test_tanpa_lot_memakai_bahasa_anggaran():
    text = "\n".join(market._order_ticket(_plan(lots=0), _Deps))
    assert "sesuaikan anggaran" in text

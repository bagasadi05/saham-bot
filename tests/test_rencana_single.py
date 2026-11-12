"""Tes offline P13: /rencana satu emiten — parser anggaran, sizing, teks."""
from __future__ import annotations

import asyncio
import types

import numpy as np
import pytest

import bot
from core import rencana, store
from tests.conftest import make_prices


# ------------------------------------------------------------- parser anggaran
@pytest.mark.parametrize("tok,nilai", [
    ("5000000", 5_000_000),
    ("5.000.000", 5_000_000),
    ("5jt", 5_000_000),
    ("5juta", 5_000_000),
    ("1,5jt", 1_500_000),
    ("500rb", 500_000),
    ("250k", 250_000),
    ("rp 2.500.000", 2_500_000),
])
def test_parse_rupiah_bentuk_umum(tok, nilai):
    assert bot._parse_rupiah(tok) == nilai


@pytest.mark.parametrize("tok", ["", "abc", "0", "-5000", "5jt6", "juta"])
def test_parse_rupiah_menolak_sampah(tok):
    assert bot._parse_rupiah(tok) is None


# ------------------------------------------------------------ level & sizing
def _cand(**over):
    c = {"ticker": "AAAA", "name": "PT Contoh", "board": "Utama",
         "sector": "Financials", "price": 1000.0, "atr": 30.0,
         "warnings": [], "vetoes": []}
    c.update(over)
    return c


def test_price_levels_sama_dengan_make_plan():
    c = _cand()
    lv = rencana.price_levels(c)
    plan = rencana.make_plan(c, 10_000_000)
    assert (lv["entry"], lv["sl"], lv["tp"]) == (plan["entry"], plan["sl"],
                                                 plan["tp"])
    assert lv["entry"] == 1000 and lv["sl"] == 970 and lv["tp"] == 1060
    assert lv["rr"] == 2.0
    assert lv["chase_above"] == 1015          # ceil(1015, tick 5)


def test_budget_plan_membagi_uang_sesuai_anggaran():
    plan = rencana.budget_plan(_cand(), 5_000_000, 10_000_000)
    assert plan["ok"] is True
    assert plan["lots"] == 49                 # 49x100x1000 = 4,9 jt + fee ≤ 5 jt
    assert plan["shares"] == 4900
    assert plan["value"] == 4_900_000
    assert plan["buy_cost"] == 4_907_350      # fee beli 0,15%
    assert plan["cash_left"] == 92_650
    assert plan["pct_modal"] == pytest.approx(49.07, abs=0.01)
    # rugi di SL termasuk fee beli & fee jual
    harap = 4_900_000 - 4900 * 970 + 4_900_000 * 0.0015 + 4900 * 970 * 0.0025
    assert plan["max_loss"] == pytest.approx(harap, abs=1)
    assert plan["max_loss_pct"] == pytest.approx(harap / 10_000_000 * 100,
                                                 abs=0.02)
    # untung di TP termasuk fee jual
    assert plan["profit_tp"] == pytest.approx(273_665, abs=1)
    assert plan["rr"] == 2.0


def test_budget_plan_peringatkan_aturan_risiko_1persen():
    plan = rencana.budget_plan(_cand(), 5_000_000, 10_000_000)
    assert plan["lots_risk"] == 33            # 1% x 10 jt / risiko 30/lembar
    assert any("di atas aturan 1%" in w for w in plan["warnings"])


def test_budget_plan_jujur_kalau_uang_kurang_dari_1_lot():
    plan = rencana.budget_plan(_cand(), 50_000, 10_000_000)
    assert plan["ok"] is False
    assert plan["lots"] == 0 and plan["shares"] == 0
    assert plan["lot_value"] == 100_000
    assert plan["min_budget"] == 100_150      # 1 lot + fee beli
    assert "1 lot" in plan["skip_reason"]


# ------------------------------------------------------------------- handler
class _Msg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self

    async def reply_photo(self, **kw):  # pragma: no cover - tidak dipakai
        return self


class _Chat:
    def __init__(self, cid: int = 100):
        self.id = cid


class _User:
    id = 100


class _Update:
    def __init__(self, text: str, cid: int = 100):
        self.effective_message = _Msg(text)
        self.effective_chat = _Chat(cid)
        self.effective_user = _User()


def _analyzed(price=1000.0, atr=30.0, volume=10_000_000):
    ind = make_prices(np.linspace(price * 0.9, price, 80), volume=volume)
    ind["atr14"] = atr
    return {
        "res": {"ticker": "BBCA",
                "snap": {"close": price, "rsi": 60.0, "ret20d": 2.0},
                "ind": ind},
        "fund": {"pct": 15.0, "label": "BAIK", "notes": []},
        "fund_raw": {"sector": "Financials", "per": 10.0, "pbv": 1.2,
                     "roe": 15.0, "debt_eq": 50.0, "div_yield": 2.0},
        "nsent": {"pct": 10.0, "label": "NETRAL", "n": 1, "details": []},
    }


@pytest.fixture
def jalankan(monkeypatch, tmp_store):
    """Jalankan handler /rencana dengan data sintetis (tanpa jaringan)."""
    async def fake_gather(t):
        return _analyzed()

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    bot._last_used.clear()

    def run(text: str):
        upd = _Update(text)
        asyncio.run(bot.cmd_rencana(upd, types.SimpleNamespace()))
        return upd.effective_message.replies

    return run


def _teks(replies) -> str:
    return "\n".join(t for t, _ in replies)


def test_rencana_satu_emiten_menampilkan_lot_biaya_dan_risiko(jalankan):
    store.set_modal(100, 10_000_000)
    teks = _teks(jalankan("/rencana BBCA 5000000"))
    assert "RENCANA BELI — BBCA" in teks
    assert "49 lot" in teks and "4.900 lembar" in teks
    assert "4.907.350" in teks              # nilai + fee beli
    assert "49,1%" in teks                  # porsi modal (1 desimal)
    assert "Kalau kena SL: rugi" in teks
    assert "Kalau kena TP: untung bersih" in teks
    assert "RR 1:2" in teks
    assert "maks <b>33 lot</b>" in teks     # aturan 1% modal


def test_rencana_tanpa_argumen_memberi_contoh(jalankan):
    teks = _teks(jalankan("/rencana"))
    assert "/rencana BBCA 5000000" in teks
    assert "/paket" in teks


def test_rencana_angka_saja_mengarahkan_ke_paket(jalankan):
    teks = _teks(jalankan("/rencana 3"))
    assert "/paket" in teks


def test_rencana_anggaran_tidak_valid_tidak_menebak(jalankan):
    teks = _teks(jalankan("/rencana BBCA abc"))
    assert "tidak saya mengerti" in teks
    assert "/rencana BBCA 5000000" in teks


def test_rencana_pakai_modal_tersimpan_kalau_anggaran_kosong(jalankan):
    store.set_modal(100, 10_000_000)
    teks = _teks(jalankan("/rencana BBCA"))
    assert "modal tersimpan" in teks
    assert "99 lot" in teks                 # 10 jt / (1000x100), fee beli ikut


def test_rencana_uang_kurang_dijawab_jujur(jalankan):
    store.set_modal(100, 10_000_000)
    teks = _teks(jalankan("/rencana BBCA 50000"))
    assert "belum cukup untuk 1 lot" in teks
    assert "100.150" in teks                # harga 1 lot + fee


def test_rencana_tetap_jujur_bila_gagal_saringan(monkeypatch, tmp_store):
    """Permintaan eksplisit tetap dilayani, tapi vetonya ditulis terang."""
    async def fake_gather(t):
        return _analyzed(volume=1_000_000)   # likuiditas < Rp2 miliar/hari

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    bot._last_used.clear()
    store.set_modal(100, 10_000_000)
    upd = _Update("/rencana BBCA 5000000")
    asyncio.run(bot.cmd_rencana(upd, types.SimpleNamespace()))
    teks = _teks(upd.effective_message.replies)
    assert "Tidak lolos saringan" in teks
    assert "likuiditas" in teks

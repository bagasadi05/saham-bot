"""Pengujian fitur filter Saham Syariah (ISSI / DES OJK) vs Semua Saham."""
from __future__ import annotations

import asyncio

from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import store, syariah
from handlers import info as info_handler
from handlers import menu as menu_handler
from handlers import sinyal as sinyal_handler


class _FakeMsg:
    def __init__(self):
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))

    async def edit_text(self, text, **kw):
        self.edits.append((text, kw))


def _make_update(chat_id: int = 9901, user_id: int = 9901, cb_data: str | None = None):
    msg = _FakeMsg()
    cb = None
    if cb_data is not None:
        cb = SimpleNamespace(
            data=cb_data,
            message=msg,
            from_user=SimpleNamespace(id=user_id),
            answer=AsyncMock(),
        )
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        callback_query=cb,
    ), msg


def test_syariah_dataset_and_helpers():
    meta = syariah.metadata()
    assert meta["count"] == 632
    assert syariah.is_syariah("TLKM")
    assert syariah.is_syariah("tlkm.jk")
    assert syariah.is_syariah("BRIS.JK")
    assert syariah.is_syariah("WBSA")
    assert not syariah.is_syariah("BBCA")
    assert not syariah.is_syariah("BBRI.JK")
    assert not syariah.is_syariah("HMSP")
    assert not syariah.is_syariah("")

    assert syariah.syariah_badge("TLKM.JK") == "☪️ Syariah"
    assert syariah.syariah_badge("BBCA.JK") == ""

    filtered = syariah.filter_tickers(["BBCA.JK", "TLKM.JK", "BBRI.JK", "BRIS.JK"])
    assert filtered == ["TLKM.JK", "BRIS.JK"]
    assert syariah.filter_tickers(["BBCA.JK", "TLKM.JK"], syariah_only=False) == [
        "BBCA.JK", "TLKM.JK"
    ]

    rows = [
        {"ticker": "BBCA.JK", "signal": "BELI"},
        {"ticker": "TLKM.JK", "signal": "BELI"},
        {"ticker": "HMSP.JK", "signal": "JUAL"},
    ]
    assert [r["ticker"] for r in syariah.filter_rows(rows)] == ["TLKM.JK"]

    seed = syariah.syariah_universe(10)
    assert len(seed) == 10
    assert all(t.endswith(".JK") and syariah.is_syariah(t) for t in seed)

    assert "TERMASUK SAHAM SYARIAH" in syariah.format_check_text("TLKM")
    assert "BUKAN SAHAM SYARIAH" in syariah.format_check_text("BBCA")
    assert "tidak valid" in syariah.format_check_text("!!!")


def test_store_syariah_mode_persistence(tmp_store):
    assert store.get_syariah_mode(777) is False
    assert store.set_syariah_mode(777, True) is True
    assert store.get_syariah_mode(777) is True
    assert store.get_syariah_mode(888) is False
    assert store.set_syariah_mode(777, False) is False
    assert store.get_syariah_mode(777) is False


def test_menu_markup_shows_syariah_toggle():
    kb_off = menu_handler.main_menu_markup("expert", syariah_only=False)
    labels_off = [b.text for row in kb_off.inline_keyboard for b in row]
    assert any("Semua" in lbl for lbl in labels_off)
    kb_pem = menu_handler.main_menu_markup("pemula", syariah_only=False)
    assert sum(len(row) for row in kb_pem.inline_keyboard) <= 8

    kb_on = menu_handler.main_menu_markup("expert", syariah_only=True)
    labels_on = [b.text for row in kb_on.inline_keyboard for b in row]
    assert any("Syariah" in lbl for lbl in labels_on)


def test_do_syariah_command_and_callbacks(tmp_store):

    deps = SimpleNamespace(
        ALLOWED_IDS=set(),
        _pending_clear=lambda _cid: None,
        _safe_edit=AsyncMock(side_effect=lambda msg, text, **kw: msg.edits.append((text, kw))),
        home_markup=menu_handler.home_markup,
    )

    # /syariah (status default)
    upd, msg = _make_update(501)
    asyncio.run(info_handler.do_syariah(upd, SimpleNamespace(args=[]), deps))
    assert "Semua Saham" in msg.replies[-1][0]

    # /syariah on
    upd, msg = _make_update(501)
    asyncio.run(info_handler.do_syariah(upd, SimpleNamespace(args=["on"]), deps))
    assert store.get_syariah_mode(501) is True
    assert "Saham Syariah diaktifkan" in msg.replies[-1][0]

    # /syariah cek TLKM & BBCA
    upd, msg = _make_update(501)
    asyncio.run(info_handler.do_syariah(upd, SimpleNamespace(args=["cek", "TLKM"]), deps))
    assert "TERMASUK SAHAM SYARIAH" in msg.replies[-1][0]

    upd, msg = _make_update(501)
    asyncio.run(info_handler.do_syariah(upd, SimpleNamespace(args=["BBCA"]), deps))
    assert "BUKAN SAHAM SYARIAH" in msg.replies[-1][0]

    # /syariah off
    upd, msg = _make_update(501)
    asyncio.run(info_handler.do_syariah(upd, SimpleNamespace(args=["off"]), deps))
    assert store.get_syariah_mode(501) is False

    # Callback syariah:menu & syariah:set:on
    upd, msg = _make_update(501, cb_data="syariah:menu")
    asyncio.run(menu_handler.menu_callback(upd, SimpleNamespace(), deps))
    assert "Filter Jenis Saham" in msg.edits[-1][0]

    upd, msg = _make_update(501, cb_data="syariah:set:on")
    asyncio.run(menu_handler.menu_callback(upd, SimpleNamespace(), deps))
    assert store.get_syariah_mode(501) is True
    assert "Filter Saham Syariah AKTIF" in msg.edits[-1][0]


def test_sinyal_filters_syariah_rows(tmp_store, monkeypatch):
    store.set_syariah_mode(601, True)

    sample_rows = [
        {"ticker": "BBCA.JK", "signal": "BELI", "rel_pct": 95, "close": 9000, "ret1d": 1.0, "ret20d": 5.0},
        {"ticker": "TLKM.JK", "signal": "BELI", "rel_pct": 90, "close": 3100, "ret1d": 0.8, "ret20d": 4.0},
        {"ticker": "HMSP.JK", "signal": "JUAL", "rel_pct": 10, "close": 700, "ret1d": -1.0, "ret20d": -5.0},
    ]
    monkeypatch.setattr(store, "load_market_scan", lambda: sample_rows)
    monkeypatch.setattr(store, "market_scan_meta", lambda: {"trade_date": "2026-11-23"})

    deps = SimpleNamespace(
        cooldown_ok=AsyncMock(return_value=True),
        _kualitas_map=AsyncMock(return_value={}),
        _scan_when=lambda _m: "2026-11-23",
        _SINYAL_DISCLAIMER="DYOR",
        reply_long=AsyncMock(side_effect=lambda m, text, **kw: m.replies.append((text, kw))),
        home_markup=menu_handler.home_markup,
    )
    upd, msg = _make_update(601)
    asyncio.run(sinyal_handler.do(upd, [], deps))
    out = msg.replies[-1][0]
    assert "SYARIAH" in out
    assert "TLKM" in out
    assert "BBCA" not in out
    assert "HMSP" not in out

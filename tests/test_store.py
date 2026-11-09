"""Test penyimpanan SQLite: watchlist, alert, paper trading, modal, auto."""
from __future__ import annotations

from core import store


def test_watchlist_add_remove(tmp_store):
    ok, wl = store.watchlist_add(1, "BBCA")
    assert ok and wl == ["BBCA"]
    ok, wl = store.watchlist_add(1, "BBCA")
    assert not ok and wl == ["BBCA"]
    ok, wl = store.watchlist_remove(1, "BBCA")
    assert ok and wl == []
    ok, wl = store.watchlist_remove(1, "BBCA")
    assert not ok and wl == []
    assert tmp_store.exists()


def test_watchlist_terpisah_per_chat(tmp_store):
    store.watchlist_add(1, "BBCA")
    store.watchlist_add(2, "TLKM")
    assert store.get_watchlist(1) == ["BBCA"]
    assert store.get_watchlist(2) == ["TLKM"]


def test_alert_digabung_dan_dihapus(tmp_store):
    assert store.set_alert(1, "BBCA", {"jump"}) == {"jump"}
    assert store.set_alert(1, "BBCA", {"breakout"}) == {"jump", "breakout"}
    assert store.get_alerts(1) == {"BBCA": ["breakout", "jump"]}
    assert store.remove_alert(1, "BBCA") is True
    assert store.remove_alert(1, "BBCA") is False


def test_modal_default_dan_ubah(tmp_store):
    assert store.get_modal(9) == store.DEFAULT_MODAL
    store.set_modal(9, 5_000_000)
    assert store.get_modal(9) == 5_000_000


def test_paper_trading_disimpan(tmp_store):
    rec = {"entry": 5000, "sl": 4800, "tp": 5400, "status": "open"}
    store.set_paper_record(1, "BBCA", rec)
    assert store.get_paper(1) == {"BBCA": rec}


def test_auto_flag(tmp_store):
    assert store.get_auto(1) is False
    store.set_auto(1, True)
    assert store.get_auto(1) is True
    assert store.get_auto_chats() == ["1"]


def test_json_lama_rusak_tidak_crash(tmp_path, monkeypatch):
    legacy = tmp_path / "store.json"
    legacy.write_text("{ini bukan json", encoding="utf-8")
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "store.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(legacy))
    assert store.get_watchlist(1) == []
    ok, wl = store.watchlist_add(1, "BBCA")
    assert ok and wl == ["BBCA"]


def test_db_folder_otomatis_dibuat(tmp_path, monkeypatch):
    nested = tmp_path / "sub" / "folder" / "store.db"
    monkeypatch.setattr(store, "STORE_DB", str(nested))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / "none.json"))
    ok, wl = store.watchlist_add(1, "BBCA")
    assert ok and wl == ["BBCA"]
    assert nested.exists()

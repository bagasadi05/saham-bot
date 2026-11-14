"""Tes offline persistensi mode pengguna ('pemula'|'expert') & onboarding di store."""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from core import store


def test_user_mode_defaults_to_pemula_without_writing(tmp_store):
    assert store.SCHEMA_VERSION == 6
    assert store.DEFAULT_USER_MODE == "pemula"
    assert store.USER_MODES == ("pemula", "expert")

    assert store.get_user_mode(101) == "pemula"
    assert store.has_user_mode(101) is False

    with store._db() as conn:
        assert store._user_version(conn) == 6
        chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        settings = conn.execute("SELECT COUNT(*) FROM chat_settings").fetchone()[0]
    assert chats == 0
    assert settings == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pemula", "pemula"),
        ("Pemula", "pemula"),
        ("  beginner  ", "pemula"),
        ("NEWBIE", "pemula"),
        ("expert", "expert"),
        ("EXPERT", "expert"),
        (" pro ", "expert"),
        ("Mahir", "expert"),
        ("cepat", "expert"),
    ],
)
def test_normalize_user_mode_aliases(raw, expected):
    assert store.normalize_user_mode(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "trader", "admin", "proo", None, 123])
def test_normalize_user_mode_rejects_invalid(raw):
    with pytest.raises(ValueError):
        store.normalize_user_mode(raw)


def test_set_get_has_user_mode_and_chat_isolation(tmp_store):
    assert store.set_user_mode(100, "pro") == "expert"
    assert store.has_user_mode(100) is True
    assert store.get_user_mode(100) == "expert"
    assert store.get_user_mode("100") == "expert"

    # Chat lain tetap pada default pemula & belum eksplisit memilih.
    assert store.has_user_mode(200) is False
    assert store.get_user_mode(200) == "pemula"

    # Switch kembali ke pemula via alias tetap tercatat eksplisit.
    assert store.set_user_mode("100", "beginner") == "pemula"
    assert store.has_user_mode(100) is True
    assert store.get_user_mode(100) == "pemula"

    # Nilai invalid tidak menulis baris baru maupun menimpa nilai lama.
    assert store.set_user_mode(100, "expert") == "expert"
    with pytest.raises(ValueError):
        store.set_user_mode(100, "tidak_valid")
    assert store.get_user_mode(100) == "expert"

    with pytest.raises(ValueError):
        store.set_user_mode(999, "tidak_valid")
    assert store.has_user_mode(999) is False


def test_user_mode_handles_corrupt_or_legacy_setting_value(tmp_store):
    with store._db() as conn:
        store._ensure_chat(conn, 300)
        conn.execute(
            "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?)",
            ("300", "user_mode", "sampah"),
        )
        store._ensure_chat(conn, 301)
        conn.execute(
            "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?)",
            ("301", "user_mode", "PRO"),
        )

    assert store.get_user_mode(300) == "pemula"
    assert store.has_user_mode(300) is False
    assert store.get_user_mode(301) == "expert"
    assert store.has_user_mode(301) is True


def test_onboard_state_merge_delete_none_and_clear(tmp_store):
    assert store.get_onboard_state(100) == {}

    s1 = store.set_onboard_state(100, stage=1, goal="beli")
    assert s1 == {"stage": 1, "goal": "beli"}
    assert store.get_onboard_state(100) == {"stage": 1, "goal": "beli"}

    # Merge mempertahankan key lama sambil memperbarui key baru.
    s2 = store.set_onboard_state("100", stage=3, ticker="BBCA")
    assert s2 == {"stage": 3, "goal": "beli", "ticker": "BBCA"}
    assert store.get_onboard_state(100) == {"stage": 3, "goal": "beli", "ticker": "BBCA"}

    # Nilai None menghapus field tertentu tanpa menyentuh field lain.
    s3 = store.set_onboard_state(100, stage=1, goal="cek", ticker=None)
    assert s3 == {"stage": 1, "goal": "cek"}
    assert store.get_onboard_state(100) == {"stage": 1, "goal": "cek"}
    assert "ticker" not in store.get_onboard_state(100)

    # Chat lain terisolasi.
    assert store.get_onboard_state(200) == {}
    store.set_onboard_state(200, stage=2, goal="jual")
    assert store.get_onboard_state(100) == {"stage": 1, "goal": "cek"}

    # Clear hanya menghapus onboard_state milik chat target.
    assert store.clear_onboard_state(100) is None
    assert store.get_onboard_state(100) == {}
    assert store.get_onboard_state(200) == {"stage": 2, "goal": "jual"}

    # Clear berulang saat sudah kosong tetap aman.
    assert store.clear_onboard_state(100) is None
    assert store.get_onboard_state(100) == {}


@pytest.mark.parametrize("raw_val", ["{rusak", "", "[1, 2, 3]", "123", '"teks"', "null"])
def test_onboard_state_handles_invalid_or_non_dict_json(tmp_store, raw_val):
    with store._db() as conn:
        store._ensure_chat(conn, 400)
        conn.execute(
            "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?)",
            ("400", "onboard_state", raw_val),
        )

    assert store.get_onboard_state(400) == {}

    # Pembaruan setelah JSON rusak menimpa bersih dengan dict valid.
    merged = store.set_onboard_state(400, stage=2, goal="beli")
    assert merged == {"stage": 2, "goal": "beli"}
    assert store.get_onboard_state(400) == {"stage": 2, "goal": "beli"}


def test_chat_settings_keys_do_not_clobber_each_other(tmp_store):
    store.set_user_mode(500, "expert")
    store.set_onboard_state(500, stage=4, goal="beli", ticker="BBRI")
    store.set_risk_limits(500, 0.20, 0.35)

    # Reset risk limits tidak menghapus user_mode maupun onboard_state.
    store.set_risk_limits(500, None, None)
    assert store.get_user_mode(500) == "expert"
    assert store.get_onboard_state(500) == {"stage": 4, "goal": "beli", "ticker": "BBRI"}

    # Clear onboard_state tidak menghapus user_mode maupun risk limits.
    store.set_risk_limits(500, 0.15, 0.30)
    store.clear_onboard_state(500)
    assert store.get_user_mode(500) == "expert"
    assert store.get_risk_limits(500) == (0.15, 0.30)


def test_schema_v6_compatibility_and_v5_upgrade(tmp_path, monkeypatch):
    db = tmp_path / "legacy_v5.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chats (
            chat_id TEXT PRIMARY KEY,
            modal   INTEGER NOT NULL DEFAULT 10000000,
            auto    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE watchlist (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE alerts (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, kinds TEXT NOT NULL DEFAULT '[]', active TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (chat_id, symbol));
        CREATE TABLE paper (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, rec TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE score_bands (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, band TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE div_reminders (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, ex_date TEXT NOT NULL, days INTEGER NOT NULL, PRIMARY KEY (chat_id, symbol, ex_date, days));
        CREATE TABLE market_scan (ticker TEXT PRIMARY KEY, name TEXT, board TEXT, trade_date TEXT, price REAL, ret1d REAL, ret5d REAL, ret20d REAL, rsi REAL, macd_hist REAL, sma20 REAL, sma50 REAL, sma200 REAL, atr REAL, vol_avg20 REAL, value_avg20 REAL, tech_score REAL, rel_pct REAL, signal TEXT NOT NULL, reason TEXT, entry REAL, sl REAL, tp REAL);
        CREATE TABLE scan_history (trade_date TEXT NOT NULL, ticker TEXT NOT NULL, signal TEXT NOT NULL, price REAL, entry REAL, sl REAL, tp REAL, atr REAL, rel_pct REAL, reason TEXT, recorded_at TEXT NOT NULL, closed_at TEXT, bars INTEGER, fwd_ret REAL, r_multiple REAL, outcome TEXT, PRIMARY KEY (trade_date, ticker));
        CREATE TABLE scan_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE plan_cache (cache_key TEXT PRIMARY KEY, trade_date TEXT, created_at TEXT, payload TEXT NOT NULL);
        CREATE TABLE real_positions (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, lots INTEGER NOT NULL, avg_price REAL NOT NULL, sector TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE real_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL, lots INTEGER NOT NULL, price REAL NOT NULL, trade_date TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE zone_state (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, zone TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE signal_context (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, source TEXT NOT NULL, signal_price REAL NOT NULL, sl REAL, tp REAL, suggested_lots INTEGER, trade_date TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE real_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL, lots INTEGER NOT NULL, exec_price REAL NOT NULL, signal_price REAL, sl REAL, tp REAL, source TEXT NOT NULL DEFAULT 'manual', slippage_rp REAL, slippage_pct REAL, slippage_cost REAL, avg_entry REAL, gross_pnl REAL, net_pnl REAL, net_pnl_pct REAL, trade_date TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO chats(chat_id, modal, auto) VALUES ('77', 5000000, 0);
        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(store, "STORE_DB", str(db))
    monkeypatch.setattr(store, "_ready_for", None)

    assert store.get_user_mode(77) == "pemula"
    assert store.has_user_mode(77) is False
    assert store.get_onboard_state(77) == {}

    assert store.set_user_mode(77, "mahir") == "expert"
    assert store.set_onboard_state(77, stage=5, goal="beli", ticker="TLKM") == {
        "stage": 5,
        "goal": "beli",
        "ticker": "TLKM",
    }

    with store._db() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 6
        cols = [r["name"] for r in c.execute("PRAGMA table_info(chats)").fetchall()]
        assert cols == ["chat_id", "modal", "auto"]
    assert store.get_modal(77) == 5_000_000

    # Backup & restore menjaga user_mode, onboard_state, dan PRAGMA user_version == 6.
    bak = store.backup_to(str(tmp_path / "backups"), keep=2)
    restored = tmp_path / "restored.db"
    shutil.copy(bak, restored)
    monkeypatch.setattr(store, "STORE_DB", str(restored))
    monkeypatch.setattr(store, "_ready_for", None)

    assert store.get_user_mode(77) == "expert"
    assert store.has_user_mode(77) is True
    assert store.get_onboard_state(77) == {"stage": 5, "goal": "beli", "ticker": "TLKM"}
    with store._db() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 6

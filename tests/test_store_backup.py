"""D1-D2: versi skema, migrasi bernomor, backup + restore + rotasi."""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3

import bot
from core import store


def test_schema_version_is_stamped(tmp_store):
    with store._db() as conn:
        assert store._user_version(conn) == store.SCHEMA_VERSION


def test_migrations_recreate_missing_index(tmp_store):
    with store._db() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_scan_history_open")
        store._run_migrations(conn, 0)
        assert store._user_version(conn) == store.SCHEMA_VERSION
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_scan_history_open'").fetchone()
        assert row is not None


def test_backup_contains_data_and_passes_integrity(tmp_store, tmp_path):
    store.watchlist_add(42, "BBCA")
    path = store.backup_to(str(tmp_path / "backups"), keep=3)

    assert os.path.exists(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
    finally:
        conn.close()
    assert rows == [("BBCA",)]


def test_backup_rotation_keeps_newest(tmp_store, tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    old = ["store-20260101T000000000000Z.db",
           "store-20260102T000000000000Z.db",
           "store-20260103T000000000000Z.db"]
    for i, name in enumerate(old):
        p = dest / name
        p.write_bytes(b"x")
        os.utime(p, (1000 + i, 1000 + i))

    store.backup_to(str(dest), keep=2)

    names = sorted(p.name for p in dest.glob("store-*.db"))
    assert len(names) == 2
    assert old[0] not in names


def test_backup_can_be_restored(tmp_store, tmp_path, monkeypatch):
    store.watchlist_add(7, "TLKM")
    path = store.backup_to(str(tmp_path / "b"), keep=2)
    restored = tmp_path / "restored.db"
    shutil.copy(path, restored)

    monkeypatch.setattr(store, "STORE_DB", str(restored))
    monkeypatch.setattr(store, "_ready_for", None)

    assert store.get_watchlist(7) == ["TLKM"]


def test_backup_job_uses_config(monkeypatch):
    calls: dict = {}

    def fake_backup(dest, keep):
        calls["args"] = (dest, keep)
        return "hasil"

    async def fake_to_thread(fn, *args):
        return fn(*args)

    monkeypatch.setattr(store, "backup_to", fake_backup)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)
    asyncio.run(bot.backup_job(None))

    assert calls["args"] == (bot.config.BACKUP_DIR, bot.config.BACKUP_KEEP)

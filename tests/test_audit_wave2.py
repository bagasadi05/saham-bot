"""Tes regresi Gelombang 2 audit_sahambot.md (D1, D2, D3, E1, E2)."""
from __future__ import annotations

from pathlib import Path

from core import btcache, guards, textutil
from handlers import sinyal
from tools import backup_db


def test_d2_cooldown_pasar_btcache_traversal_dan_csv_injection():
    assert "pasar" in guards._COOLDOWN_SECONDS
    assert ".." not in btcache._symbol_key("../../etc/passwd")
    assert "/" not in btcache._symbol_key("../../etc/passwd")
    assert sinyal._csv_safe("=HYPERLINK(1)").startswith("'")
    assert sinyal._csv_safe("Bank Central Asia") == "Bank Central Asia"


def test_d3_split_message_menyeimbangkan_blockquote():
    body = "<blockquote expandable>" + ("Baris teks panjang.\n" * 60) + "</blockquote>"
    chunks = textutil.split_message(body, limit=400)
    assert len(chunks) >= 2
    for ch in chunks:
        assert ch.count("<blockquote") == ch.count("</blockquote>")


def test_e1_e2_docker_idx_sectors_dan_backup_db_keep(tmp_store, tmp_path):
    assert "!data/idx_sectors.json" in Path(".dockerignore").read_text(encoding="utf-8")
    assert "data/idx_sectors.json" in Path("Dockerfile").read_text(encoding="utf-8")
    bdir = tmp_path / "bkp"
    rc = backup_db.main([str(bdir), "--keep", "2"])
    assert rc == 0
    assert list(bdir.glob("store-*.db"))

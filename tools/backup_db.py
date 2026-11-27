"""Cadangan manual: python tools/backup_db.py [folder] [--keep N]

Memakai fungsi yang sama dengan job harian (WAL-safe + integrity_check +
rotasi). Default folder dari env BACKUP_DIR atau "backups".
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import store  # noqa: E402


def verify_backup(path: str) -> str:
    """Verifikasi integritas SQLite hasil backup via PRAGMA quick_check."""
    import sqlite3

    with sqlite3.connect(path, timeout=10) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    res = str(row[0] if row else "")
    if res.lower() != "ok":
        raise RuntimeError(f"integritas backup gagal ({path}): {res}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cadangan SQLite SahamBuddy")
    ap.add_argument("dest", nargs="?", default=os.getenv("BACKUP_DIR", "backups"))
    ap.add_argument("--keep", type=int, default=None, help="jumlah cadangan terakhir yang disimpan")
    ap.add_argument("--verify", action="store_true", help="uji PRAGMA integrity_check pada hasil cadangan")
    args = ap.parse_args(argv)
    if args.keep is not None:
        keep = max(1, int(args.keep))
    else:
        try:
            keep = max(1, int(os.getenv("BACKUP_KEEP", "7") or 7))
        except ValueError:
            keep = 7
    path = store.backup_to(args.dest, keep)
    if args.verify:
        verify_backup(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

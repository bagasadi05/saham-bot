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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cadangan SQLite SahamBuddy")
    ap.add_argument("dest", nargs="?", default=os.getenv("BACKUP_DIR", "backups"))
    ap.add_argument("--keep", type=int, default=None, help="jumlah cadangan terakhir yang disimpan")
    args = ap.parse_args(argv)
    if args.keep is not None:
        keep = max(1, int(args.keep))
    else:
        try:
            keep = max(1, int(os.getenv("BACKUP_KEEP", "7") or 7))
        except ValueError:
            keep = 7
    path = store.backup_to(args.dest, keep)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

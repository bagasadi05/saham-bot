"""Cadangan manual: python tools/backup_db.py [folder]

Memakai fungsi yang sama dengan job harian (WAL-safe + integrity_check +
rotasi). Default folder dari env BACKUP_DIR atau "backups".
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import store  # noqa: E402


def main(argv: list[str]) -> int:
    dest = argv[0] if argv else os.getenv("BACKUP_DIR", "backups")
    keep = int(os.getenv("BACKUP_KEEP", "7") or 7)
    path = store.backup_to(dest, keep)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

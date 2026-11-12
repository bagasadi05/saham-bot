"""Kerangka pengukuran survivorship bias — sengaja gagal keras tanpa data.

Angka bias hanya sah bila kita punya keanggotaan universe point-in-time dan
daftar emiten delisting. Tanpa itu, skrip ini menolak jalan daripada
menghasilkan angka karangan.

Format `--membership` (JSON):
    [{"ticker": "BBCA", "start": "2020-01-01", "end": "2024-12-31"}, ...]
atau {"membership": [ ... ]}

Pakai:
    python research/bias.py --membership universe_membership.json
    python research/bias.py --dry-run          # tetap butuh file membership
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REQUIRED = ("ticker", "start", "end")


def load_membership(path: str) -> list[dict]:
    """Baca dan validasi daftar keanggotaan universe."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} tidak ada — butuh keanggotaan universe point-in-time "
            "dan daftar delisting sebelum menghitung bias")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    rows = raw.get("membership") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        raise ValueError("membership harus berisi daftar objek")
    clean: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"baris {i}: harus objek")
        missing = [k for k in REQUIRED if not row.get(k)]
        if missing:
            raise ValueError(f"baris {i}: field wajib hilang: {', '.join(missing)}")
        clean.append({"ticker": str(row["ticker"]).upper().replace(".JK", ""),
                      "start": str(row["start"])[:10], "end": str(row["end"])[:10]})
    return clean


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--membership", help="JSON keanggotaan universe + delisting")
    ap.add_argument("--dry-run", action="store_true",
                    help="validasi input saja, belum ada angka bias")
    args = ap.parse_args(argv)

    if not args.membership:
        print("Butuh --membership (JSON keanggotaan universe point-in-time). "
              "Tanpa itu bias tidak bisa diukur tanpa mengarang.",
              file=sys.stderr)
        return 2
    try:
        rows = load_membership(args.membership)
    except (OSError, ValueError) as exc:
        print(f"Gagal membaca membership: {exc}", file=sys.stderr)
        return 2

    tickers = sorted({r["ticker"] for r in rows})
    print(f"Membership terbaca: {len(rows)} baris, {len(tickers)} emiten.")
    if args.dry_run:
        print("Dry-run: belum ada pengukuran. Langkah berikutnya butuh data "
              "harga emiten delisting dan tanggal keluar dari universe.")
        return 0
    print("Pengukuran bias belum diimplementasikan — tambahkan data delisting "
          "lebih dulu agar angkanya bukan karangan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

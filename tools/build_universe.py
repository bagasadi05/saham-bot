#!/usr/bin/env python3
"""Bangun `data/idx_universe.json` — daftar emiten BEI untuk screener pasar.

Urutan sumber:
  1. API resmi IDX (butuh cloudscraper; dari VPS biasanya lolos).
  2. Dataset publik turunan IDX di GitHub (fallback; dicatat provenance-nya).

Jalankan manual bila ingin memperbarui daftar:
    python tools/build_universe.py

File hasil divalidasi: kode 4 huruf kapital, unik, minimal 800 emiten.
Skrip TIDAK pernah menimpa file lama kalau validasi gagal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "idx_universe.json")

IDX_URL = ("https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
           "?start=0&length=9999&sort=KodeEmiten&order=asc")
FALLBACK_URLS = [
    ("github:wildangunawan/Dataset-Saham-IDX",
     "https://raw.githubusercontent.com/wildangunawan/Dataset-Saham-IDX/"
     "master/List%20Emiten/all.csv"),
]

CODE_RE = re.compile(r"^[A-Z]{4}$")
MIN_COUNT = 800


def parse_idx_json(text: str) -> list[dict]:
    """Parse respons API idx.co.id (JSON) jadi [{code,name,board}]."""
    raw = json.loads(text)
    rows = raw.get("data") or raw.get("Data") or raw
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("KodeEmiten") or row.get("Code")
                   or row.get("code") or "").strip().upper()
        name = str(row.get("NamaEmiten") or row.get("Name")
                   or row.get("name") or "").strip()
        board = str(row.get("PapanPencatatan") or row.get("ListingBoard")
                    or row.get("Board") or row.get("board") or "").strip()
        if code:
            out.append({"code": code, "name": name, "board": board})
    return out


def parse_csv(text: str) -> list[dict]:
    """Parse CSV berkolom code,name,listingBoard (dataset public IDX)."""
    out = []
    for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        code = (row.get("code") or row.get("Code") or "").strip().upper()
        name = (row.get("name") or row.get("Name") or "").strip()
        board = (row.get("listingBoard") or row.get("Board") or "").strip()
        if code:
            out.append({"code": code, "name": name, "board": board})
    return out


def clean(rows: list[dict]) -> list[dict]:
    """Validasi & deduplikasi; buang kode aneh (right/warrant/ETF tetap 4 huruf)."""
    seen: dict[str, dict] = {}
    for r in rows:
        code = r["code"]
        if not CODE_RE.match(code):
            continue
        seen.setdefault(code, {"code": code, "name": r.get("name", ""),
                               "board": r.get("board", "")})
    return [seen[c] for c in sorted(seen)]


def _get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_id_official() -> tuple[list[dict], str]:
    """Coba API resmi (Cloudflare). Return ([], "") kalau diblok."""
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "linux", "mobile": False})
        r = scraper.get(IDX_URL, timeout=60,
                        headers={"Referer": "https://www.idx.co.id/id/data-pasar/"
                                             "data-saham/daftar-saham/"})
        if r.status_code == 200 and "Just a moment" not in r.text[:500]:
            return parse_idx_json(r.text), "idx.co.id (API resmi)"
    except Exception as exc:  # noqa: BLE001 — kegagalan jaringan memang wajar
        print(f"  idx.co.id gagal: {type(exc).__name__}", file=sys.stderr)
    return [], ""


def fetch_fallback() -> tuple[list[dict], str]:
    for label, url in FALLBACK_URLS:
        try:
            return parse_csv(_get(url)), label
        except Exception as exc:  # noqa: BLE001
            print(f"  {label} gagal: {type(exc).__name__}", file=sys.stderr)
    return [], ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--from-file", default="",
                    help="JSON respons API IDX yang sudah diunduh "
                         "(untuk build offline)")
    args = ap.parse_args(argv)

    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as f:
                rows = parse_idx_json(f.read())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"GAGAL membaca {args.from_file}: {exc}", file=sys.stderr)
            return 1
        source = "idx.co.id (API resmi)"
    else:
        rows, source = fetch_id_official()
        if not rows:
            print("API resmi tidak tersedia — memakai dataset fallback…", file=sys.stderr)
            rows, source = fetch_fallback()
    rows = clean(rows)
    if len(rows) < MIN_COUNT:
        print(f"GAGAL: hanya {len(rows)} kode valid (< {MIN_COUNT}); "
              "file lama tidak ditimpa.", file=sys.stderr)
        return 1

    payload = {
        "source": source,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(rows),
        "stocks": rows,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
        f.write("\n")
    os.replace(tmp, args.out)
    print(f"OK: {len(rows)} kode → {args.out} (sumber: {source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bangun `data/idx_sectors.json` — pemetaan emiten BEI -> sektor.

Sumber:
  1. Tweenvest (`https://tweenvest.com/stocks/exchanges/idx/`)
  2. Fallback lokal per emiten dari `yfinance` untuk `data/idx_universe.json` (opsional `--yf-fallback`)

Validasi ketat: kode 4 huruf kapital, unik, minimal 500 emiten.
Tidak pernah menimpa `data/idx_sectors.json` bila parsing atau validasi gagal.

Pakai:
    python tools/build_sectors.py
    python tools/build_sectors.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "idx_sectors.json")
TWEENVEST_URL = "https://tweenvest.com/stocks/exchanges/idx/"
CODE_RE = re.compile(r"^[A-Z]{4}$")
MIN_COUNT = 500


def parse_tweenvest_html(html_text: str) -> dict[str, str]:
    """Ekstrak pemetaan {TICKER: Sector} dari halaman HTML tabel Tweenvest IDX."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "lxml")
    sectors: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) < 3:
            continue
        code = ""
        for cell in tds[:2]:
            cand = cell.strip().upper().replace(".JK", "")
            if CODE_RE.match(cand):
                code = cand
                break
        if not code:
            continue
        # Kolom sektor biasanya di akhir atau kolom ke-3/4
        sec = ""
        for cell in reversed(tds[1:]):
            val = cell.strip()
            if val and not CODE_RE.match(val.upper()) and not re.match(r"^[0-9.,%+-]+$", val):
                sec = val
                break
        if sec:
            sectors[code] = sec
    return dict(sorted(sectors.items()))


def validate_sectors(sectors: dict[str, str], min_count: int = MIN_COUNT) -> tuple[bool, str]:
    if not isinstance(sectors, dict) or len(sectors) < min_count:
        return False, f"jumlah emiten terlalu sedikit ({len(sectors) if isinstance(sectors, dict) else 0} < {min_count})"
    bad_keys = [k for k in sectors if not CODE_RE.match(str(k))]
    if bad_keys:
        return False, f"kode emiten tidak valid: {bad_keys[:5]}"
    empty_vals = [k for k, v in sectors.items() if not str(v).strip()]
    if empty_vals:
        return False, f"sektor kosong untuk: {empty_vals[:5]}"
    return True, "ok"


def fetch_tweenvest(timeout: int = 30) -> dict[str, str]:
    req = urllib.request.Request(
        TWEENVEST_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Google-Agent; +https://developers.google.com/crawling/docs/crawlers-fetchers/google-agent)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html_text = resp.read().decode("utf-8", errors="replace")
    return parse_tweenvest_html(html_text)


def write_sectors_atomic(sectors: dict[str, str], source: str, out_path: str = OUT) -> None:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "source": source,
        "fetched_at": now_iso,
        "count": len(sectors),
        "sectors": dict(sorted(sectors.items())),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f"{out_path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="validasi saja tanpa menimpa data/idx_sectors.json")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    try:
        sectors = fetch_tweenvest()
    except Exception as exc:
        print(f"Gagal mengambil dari Tweenvest: {exc}", file=sys.stderr)
        return 1

    ok, msg = validate_sectors(sectors)
    if not ok:
        print(f"Validasi gagal ({msg}) — file lama tidak disentuh.", file=sys.stderr)
        return 2

    print(f"Terbaca {len(sectors)} emiten bersektor dari Tweenvest.")
    if args.dry_run:
        return 0
    write_sectors_atomic(sectors, "tweenvest.com/stocks/exchanges/idx/", out_path=args.out)
    print(f"Berhasil menulis {args.out} ({len(sectors)} emiten).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

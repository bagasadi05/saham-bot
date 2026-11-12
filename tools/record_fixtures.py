"""Rekam fixture data nyata untuk tes replay (butuh internet).

Fixture sintetis di `tests/fixtures/` menjaga bentuk kolom yang kita konsumsi,
tetapi tidak menangkap perubahan skema di sisi Yahoo/BEI. Jalankan alat ini
sekali di mesin yang punya akses jaringan, lalu commit hasilnya:

    python tools/record_fixtures.py --ticker BBCA --rows 5

Yang ditulis:
    tests/fixtures/ohlcv_<TICKER>_normalized.csv   (60 bar hari terakhir)
    tests/fixtures/idx_stock_summary.json          (N baris pertama)
    tests/fixtures/idx_index_summary.json          (semua indeks)

Tidak ada token/kredensial yang disimpan; nama emiten dipertahankan karena
data publik. Kalau salah satu sumber gagal, alat ini melaporkannya dan tetap
menulis yang berhasil — tidak mengarang data pengganti.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIX = ROOT / "tests" / "fixtures"


def record_ohlcv(ticker: str, period: str, rows: int) -> bool:
    from core import data

    df = data.fetch_history(ticker, period)
    if df is None or df.empty:
        print(f"! {ticker}: data harga tidak tersedia")
        return False
    df = df.tail(rows)
    df.index.name = "date"
    path = FIX / f"ohlcv_{ticker.upper().replace('.JK', '')}_normalized.csv"
    df.to_csv(path, float_format="%.2f")
    print(f"✓ {path.relative_to(ROOT)} ({len(df)} baris)")
    return True


def record_stock_summary(rows: int) -> bool:
    from core import bandar

    try:
        data = bandar.get_stock_summary(bandar.last_trading_date(),
                                        max_stocks=max(rows, 10))
    except Exception as e:  # noqa: BLE001 - alat manual, laporkan apa adanya
        print(f"! ringkasan saham BEI gagal: {e}")
        return False
    keep = ("StockCode", "StockName", "PreviousPrice", "OpenPrice",
            "HighestPrice", "LowestPrice", "ClosingPrice", "Volume", "Value",
            "ForeignNet", "Date")
    slim = [{k: r.get(k) for k in keep if k in r} for r in data[:rows]]
    path = FIX / "idx_stock_summary.json"
    path.write_text(json.dumps({"data": slim}, ensure_ascii=False, indent=1)
                    + "\n", encoding="utf-8")
    print(f"✓ {path.relative_to(ROOT)} ({len(slim)} baris)")
    return True


def record_index_summary() -> bool:
    from core import bandar

    try:
        data = bandar.get_index_summary()
    except Exception as e:  # noqa: BLE001
        print(f"! ringkasan indeks BEI gagal: {e}")
        return False
    keep = ("IndexCode", "IndexClose", "IndexOpen", "IndexHigh", "IndexLow",
            "IndexChange", "Date")
    slim = [{k: r.get(k) for k in keep if k in r} for r in data]
    path = FIX / "idx_index_summary.json"
    path.write_text(json.dumps({"data": slim}, ensure_ascii=False, indent=1)
                    + "\n", encoding="utf-8")
    print(f"✓ {path.relative_to(ROOT)} ({len(slim)} indeks)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="BBCA")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--rows", type=int, default=5,
                    help="baris ringkasan saham yang disimpan")
    ap.add_argument("--skip-ohlcv", action="store_true")
    args = ap.parse_args()

    FIX.mkdir(parents=True, exist_ok=True)
    ok = True
    if not args.skip_ohlcv:
        ok &= record_ohlcv(args.ticker, args.period, 60)
    ok &= record_stock_summary(args.rows)
    ok &= record_index_summary()
    print("\nSelesai." if ok else "\nSelesai dengan sebagian sumber gagal.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

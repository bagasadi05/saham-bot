"""Rekam fixture data nyata untuk tes replay (butuh internet).

Fixture sintetis di `tests/fixtures/` menjaga bentuk kolom yang kita konsumsi,
tetapi tidak menangkap perubahan skema di sisi Yahoo/BEI. Jalankan alat ini
sekali di mesin yang punya akses jaringan, lalu commit hasilnya:

    python tools/record_fixtures.py --ticker BBCA --ohlcv-rows 120 --rows 5
    python tools/record_fixtures.py --verify   # audit struktur offline tanpa jaringan

Yang ditulis:
    tests/fixtures/ohlcv_<TICKER>_normalized.csv   (N bar hari terakhir)
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

    if rows < 1:
        print("! --ohlcv-rows harus >= 1")
        return False
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


def verify_fixtures(fix_dir: pathlib.Path = FIX) -> tuple[bool, list[str]]:
    """Verifikasi struktur fixture di `tests/fixtures/` secara offline."""
    import pandas as pd

    from core import bandar, data_quality, sektor

    msgs: list[str] = []
    ok = True

    ohlcv_files = sorted(fix_dir.glob("ohlcv_*_normalized.csv"))
    if not ohlcv_files:
        return False, [f"FAIL: tidak ada ohlcv_*_normalized.csv di {fix_dir}"]

    req_cols = {"open", "high", "low", "close", "volume"}
    for csv_path in ohlcv_files:
        try:
            raw_head = pd.read_csv(csv_path, nrows=1)
            if "date" not in raw_head.columns:
                ok = False
                msgs.append(f"FAIL: {csv_path.name} tidak punya kolom 'date'")
                continue
            df = pd.read_csv(csv_path, parse_dates=["date"], index_col="date")
        except Exception as exc:  # noqa: BLE001
            ok = False
            msgs.append(f"FAIL: gagal baca {csv_path.name}: {exc}")
            continue
        missing = sorted(req_cols - set(df.columns))
        if missing:
            ok = False
            msgs.append(f"FAIL: {csv_path.name} kurang kolom {missing}")
            continue
        if len(df) < 20:
            ok = False
            msgs.append(f"FAIL: {csv_path.name} hanya {len(df)} baris (< 20)")
            continue
        if not df.index.is_monotonic_increasing:
            ok = False
            msgs.append(f"FAIL: {csv_path.name} tanggal tidak urut naik")
            continue
        if df[list(req_cols)].isna().any().any() or (df["close"] <= 0).any():
            ok = False
            msgs.append(f"FAIL: {csv_path.name} mengandung NaN atau close <= 0")
            continue
        dq = data_quality.check(df)
        if not isinstance(dq, dict) or "ok" not in dq:
            ok = False
            msgs.append(f"FAIL: {csv_path.name} gagal validasi data_quality.check")
            continue
        msgs.append(f"OK: {csv_path.name} ({len(df)} baris, dq_ok={dq['ok']})")

    stock_path = fix_dir / "idx_stock_summary.json"
    if not stock_path.exists():
        ok = False
        msgs.append("FAIL: idx_stock_summary.json tidak ada")
    else:
        try:
            payload = json.loads(stock_path.read_text(encoding="utf-8"))
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                ok = False
                msgs.append("FAIL: idx_stock_summary.json field 'data' kosong/bukan list")
            else:
                req_stock = {"StockCode", "ClosingPrice", "PreviousPrice", "Volume"}
                miss = sorted(req_stock - set(rows[0]))
                if miss:
                    ok = False
                    msgs.append(f"FAIL: idx_stock_summary.json kurang field {miss}")
                else:
                    _ = bandar.to_pct(rows[0])
                    _ = bandar.foreign_fields(rows[0])
                    msgs.append(f"OK: idx_stock_summary.json ({len(rows)} baris)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            msgs.append(f"FAIL: idx_stock_summary.json korup: {exc}")

    idx_path = fix_dir / "idx_index_summary.json"
    if not idx_path.exists():
        ok = False
        msgs.append("FAIL: idx_index_summary.json tidak ada")
    else:
        try:
            payload = json.loads(idx_path.read_text(encoding="utf-8"))
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                ok = False
                msgs.append("FAIL: idx_index_summary.json field 'data' kosong/bukan list")
            else:
                req_idx = {"IndexCode", "IndexClose"}
                miss = sorted(req_idx - set(rows[0]))
                if miss:
                    ok = False
                    msgs.append(f"FAIL: idx_index_summary.json kurang field {miss}")
                else:
                    sek = sektor.summarize(rows)
                    if not isinstance(sek, dict) or "ok" not in sek:
                        ok = False
                        msgs.append("FAIL: sektor.summarize mengembalikan format tidak valid")
                    else:
                        msgs.append(f"OK: idx_index_summary.json ({len(rows)} indeks)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            msgs.append(f"FAIL: idx_index_summary.json korup: {exc}")

    return ok, msgs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="BBCA")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--ohlcv-rows", type=int, default=60,
                    help="baris OHLCV harian yang disimpan (default 60)")
    ap.add_argument("--rows", type=int, default=5,
                    help="baris ringkasan saham yang disimpan")
    ap.add_argument("--skip-ohlcv", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="verifikasi struktur fixture offline tanpa memanggil internet")
    args = ap.parse_args(argv)

    if args.verify:
        ok, msgs = verify_fixtures()
        for m in msgs:
            print(m)
        print("Verifikasi fixture: PASS" if ok else "Verifikasi fixture: FAIL")
        return 0 if ok else 1

    if (not args.skip_ohlcv and args.ohlcv_rows < 1) or args.rows < 1:
        print("! --ohlcv-rows dan --rows harus >= 1")
        return 1

    FIX.mkdir(parents=True, exist_ok=True)
    ok = True
    if not args.skip_ohlcv:
        ok &= record_ohlcv(args.ticker, args.period, args.ohlcv_rows)
    ok &= record_stock_summary(args.rows)
    ok &= record_index_summary()
    print("\nSelesai." if ok else "\nSelesai dengan sebagian sumber gagal.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

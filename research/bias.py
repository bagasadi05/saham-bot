"""Pengukuran survivorship bias — membandingkan universe statis vs point-in-time.

Angka bias dihitung dengan menyaring dataset riset sesuai keanggotaan LQ45
pada masing-masing tanggal (`start <= date <= end`), lalu menjalankan evaluasi
walk-forward pada kedua dataset (statis vs point-in-time). Emiten yang sudah
delisting dan tidak tersedia di Yahoo dicatat secara eksplisit dalam laporan.

Format `--membership` (JSON):
    [{"ticker": "BBCA", "start": "2020-01-01", "end": "2024-12-31"}, ...]
atau {"membership": [ ... ]}

Pakai:
    python research/bias.py --membership research/lq45_membership_2016-2026.json --dry-run
    python research/bias.py --membership research/lq45_membership_2016-2026.json --run
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED = ("ticker", "start")


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
        end = row.get("end")
        clean.append({"ticker": str(row["ticker"]).upper().replace(".JK", ""),
                      "start": str(row["start"])[:10],
                      "end": str(end)[:10] if end else None})
    return clean


def filter_by_membership(df: pd.DataFrame, membership: list[dict]) -> pd.DataFrame:
    """Saring dataset riset agar tiap baris hanya masuk jika ticker menjadi anggota pada tanggal itu."""
    if df is None or df.empty or not membership:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()
    dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    tickers = df["ticker"].astype(str).str.upper().str.replace(".JK", "", regex=False)

    intervals_by_ticker: dict[str, list[tuple[str, str | None]]] = {}
    for row in membership:
        t = str(row["ticker"]).upper().replace(".JK", "")
        intervals_by_ticker.setdefault(t, []).append((row["start"], row.get("end")))

    mask = pd.Series(False, index=df.index)
    for t, spans in intervals_by_ticker.items():
        t_mask = tickers == t
        if not t_mask.any():
            continue
        date_ok = pd.Series(False, index=df.index)
        for start, end in spans:
            cond = dates >= start
            if end:
                cond = cond & (dates <= end)
            date_ok = date_ok | cond
        mask = mask | (t_mask & date_ok)
    return df.loc[mask].copy().reset_index(drop=True)


def compare_static_vs_pit(
    df_static: pd.DataFrame,
    df_all: pd.DataFrame,
    membership: list[dict],
    *,
    horizon: int = 20,
    years: list[int] | None = None,
    embargo: int = 0,
) -> dict:
    """Bandingkan metrik OOS universe statis vs point-in-time."""
    from research import evaluate

    years = years or [2022, 2023, 2024, 2025, 2026]
    df_pit = filter_by_membership(df_all, membership)
    feats = [c for c in df_static.columns if c.startswith("f_")]

    def _eval_slice(sub: pd.DataFrame) -> dict:
        if sub is None or sub.empty:
            return {"n_rows": 0, "n_tickers": 0, "ic_mean": None, "wf_mean_ic": None, "q5_q1_spread": None}
        ic_s = evaluate.ic_summary(evaluate.xs_ic(sub, "score"), horizon)
        wf, oos_q, _ = evaluate.walk_forward(sub, feats, horizon, years, embargo)
        wf_sum = evaluate._wf_summary(wf)
        spread = None
        if oos_q and len(oos_q) >= 2:
            q1_row = oos_q[0]
            q5_row = oos_q[-1]
            q1 = next((q1_row[k] for k in ("excess_pct", "excess_ret_pct", "mean_pct") if q1_row.get(k) is not None), None)
            q5 = next((q5_row[k] for k in ("excess_pct", "excess_ret_pct", "mean_pct") if q5_row.get(k) is not None), None)
            if q1 is not None and q5 is not None:
                spread = round(float(q5) - float(q1), 4)
        return {
            "n_rows": int(len(sub)),
            "n_tickers": int(sub["ticker"].nunique()),
            "ic_mean": ic_s.get("mean_ic"),
            "ic_t_hac": ic_s.get("t_hac"),
            "wf_mean_ic": wf_sum.get("ic_model_mean"),
            "q5_q1_spread": spread,
        }

    st_res = _eval_slice(df_static)
    pit_res = _eval_slice(df_pit)
    bias_wf = (
        round(float(st_res["wf_mean_ic"]) - float(pit_res["wf_mean_ic"]), 4)
        if st_res["wf_mean_ic"] is not None and pit_res["wf_mean_ic"] is not None
        else None
    )
    bias_spread = (
        round(float(st_res["q5_q1_spread"]) - float(pit_res["q5_q1_spread"]), 4)
        if st_res["q5_q1_spread"] is not None and pit_res["q5_q1_spread"] is not None
        else None
    )
    return {
        "static": st_res,
        "point_in_time": pit_res,
        "bias": {
            "wf_ic_diff_static_minus_pit": bias_wf,
            "q5_q1_spread_diff_pct": bias_spread,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--membership", help="JSON keanggotaan universe + delisting")
    ap.add_argument("--dry-run", action="store_true",
                    help="validasi input saja, tanpa unduh data")
    ap.add_argument("--run", action="store_true",
                    help="jalankan perbandingan statis vs point-in-time pada data historis")
    ap.add_argument("--period", default="10y")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--out", default=os.path.join("temp", "research", "bias_report.json"))
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
    if args.dry_run or not args.run:
        print("Dry-run: pakai --run untuk menjalankan pengukuran bias statis vs point-in-time.")
        return 0

    from core import saran
    from research import evaluate

    static_tickers = list(dict.fromkeys(saran.LQ45))
    cache_dir = os.path.dirname(args.out) or os.path.join("temp", "research")
    os.makedirs(cache_dir, exist_ok=True)
    st_cache = os.path.join(cache_dir, f"dataset_{args.period}_h{args.horizon}_{len(static_tickers)}.pkl")
    all_cache = os.path.join(cache_dir, f"dataset_{args.period}_h{args.horizon}_{len(tickers)}_pit.pkl")
    if os.path.exists(st_cache):
        df_static = pd.read_pickle(st_cache)
    else:
        print(f"Membangun dataset statis ({len(static_tickers)} emiten)...")
        df_static = evaluate.build_dataset(static_tickers, args.period, args.horizon)
        df_static.to_pickle(st_cache)
    if os.path.exists(all_cache):
        df_all = pd.read_pickle(all_cache)
    else:
        print(f"Membangun dataset historis ({len(tickers)} emiten)...")
        df_all = evaluate.build_dataset(tickers, args.period, args.horizon)
        df_all.to_pickle(all_cache)
    present = set(df_all["ticker"].unique()) if not df_all.empty else set()
    missing_tickers = sorted(set(tickers) - present)

    report = compare_static_vs_pit(
        df_static, df_all, rows, horizon=args.horizon,
    )
    report["missing_or_delisted_tickers"] = missing_tickers
    report["membership_total_tickers"] = len(tickers)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

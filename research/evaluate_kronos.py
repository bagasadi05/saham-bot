"""Uji OOS Kronos (foundation model) di saham LQ45 — apakah layak diadopsi?

Pertanyaan yang dijawab: kalau Kronos diminta meramal 20 hari ke depan,
apakah ramalannya lebih baik daripada skor bot yang sekarang?

Desain (sengaja ketat):
  - Data harian yang SAMA dengan harness evaluasi skor (Yahoo, 10 tahun).
  - Titik uji bulanan (bukan harian) supaya horizon 20 hari tidak tumpang
    tindih berat; hanya tanggal setelah 2022 (OOS terhadap pengetahuan umum
    model, walau pretraining-nya tetap tidak bisa dipastikan).
  - Konteks 400 bar (~1,6 tahun) — di bawah batas 512 model.
  - Horizon 20 hari bursa; ramalan = close hari ke-20 / close terakhir - 1.
  - Metrik: IC cross-sectional Spearman per tanggal, dibandingkan langsung
    dengan skor bot dan dua baseline naif (momentum 20 hari, contrarian).

Hasil ditulis bertahap ke temp/research/kronos_results.csv, jadi kalau
dihentikan di tengah, yang sudah dihitung tidak hilang.

Pakai:
    python research/evaluate_kronos.py --tickers 3 --dates 3     # uji cepat
    python research/evaluate_kronos.py                          # penuh
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import analysis, data, saran  # noqa: E402
from vendor.kronos import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

CONTEXT = 400
MIN_TICKERS = 6


# ---------------------------------------------------------------- model

def load_predictor(device: str = "cpu") -> KronosPredictor:
    print("memuat tokenizer + Kronos-small dari HuggingFace ...", flush=True)
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    model.eval()
    return KronosPredictor(model, tok, device=device, max_context=512)


# ---------------------------------------------------------------- sampling

def sample_dates(index: pd.DatetimeIndex, start: str, every: int) -> list[int]:
    """Indeks posisi di series untuk titik uji (per `every` hari bursa)."""
    start_ts = pd.Timestamp(start)
    pos = []
    for i in range(CONTEXT, len(index) - 21):
        if index[i] >= start_ts and (i - CONTEXT) % every == 0:
            pos.append(i)
    return pos


def predict_one(predictor: KronosPredictor, df: pd.DataFrame, i: int,
                horizon: int, samples: int) -> float:
    win = df.iloc[i - CONTEXT + 1: i + 1]
    x_ts = pd.Series(pd.to_datetime(win.index))
    last = pd.Timestamp(df.index[i])
    y_idx = pd.bdate_range(last + pd.Timedelta(days=1), periods=horizon)
    y_ts = pd.Series(y_idx)

    frame = pd.DataFrame({
        "open": win["open"].to_numpy(float),
        "high": win["high"].to_numpy(float),
        "low": win["low"].to_numpy(float),
        "close": win["close"].to_numpy(float),
        "volume": win["volume"].to_numpy(float),
    })
    if "amount" not in frame:
        frame["amount"] = frame["volume"] * frame[["open", "high", "low", "close"]].mean(axis=1)

    pred = predictor.predict(frame, x_ts, y_ts, pred_len=horizon,
                             T=1.0, top_k=0, top_p=0.9,
                             sample_count=samples, verbose=False)
    return float(pred["close"].iloc[-1] / frame["close"].iloc[-1] - 1)


# ---------------------------------------------------------------- evaluasi

def xs_ic(df: pd.DataFrame, col: str, ret: str = "realized") -> pd.Series:
    out = {}
    for d, g in df.groupby("date"):
        g = g[[col, ret]].dropna()
        if len(g) < MIN_TICKERS:
            continue
        ra, rb = g[col].rank(), g[ret].rank()
        if ra.std() == 0 or rb.std() == 0:
            continue
        out[d] = float(np.corrcoef(ra, rb)[0, 1])
    return pd.Series(out, dtype=float)


def summarize(ic: pd.Series) -> dict:
    if len(ic) == 0:
        return {"n": 0, "mean_ic": None, "t": None}
    m, s = float(ic.mean()), float(ic.std(ddof=1))
    return {"n": int(len(ic)), "mean_ic": round(m, 4),
            "t": round(m / s * math.sqrt(len(ic)), 2) if s else None}


def risk_metrics(df: pd.DataFrame, col: str, q: int = 5) -> dict:
    """Return rata-rata per kuintil prediksi (di-demean per tanggal)."""
    d = df.dropna(subset=[col, "realized"]).copy()
    if len(d) < 30:
        return {}
    d["excess"] = d["realized"] - d.groupby("date")["realized"].transform("mean")
    d["q"] = d.groupby("date")[col].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False, duplicates="drop")
        if s.notna().sum() >= q else np.nan)
    d = d.dropna(subset=["q"])
    g = d.groupby("q")["excess"]
    return {f"Q{int(k) + 1}": round(float(v.mean()) * 100, 3) for k, v in g}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="10y")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--every", type=int, default=21, help="jarak antar titik uji (hari bursa)")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--samples", type=int, default=1, help="sample Monte Carlo per ramalan")
    ap.add_argument("--tickers", type=int, default=15)
    ap.add_argument("--dates", type=int, default=0, help="batasi jumlah tanggal uji (0= semua)")
    ap.add_argument("--out", default=os.path.join("temp", "research"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "kronos_results.csv")
    done: set[tuple] = set()
    if os.path.exists(csv_path):
        old = pd.read_csv(csv_path, parse_dates=["date"])
        done = {(r.ticker, r.date.normalize()) for r in old.itertuples()}
        print(f"melanjutkan: {len(done)} titik sudah ada di cache")

    tickers = list(dict.fromkeys(saran.LQ45))[: args.tickers]
    print(f"emiten: {len(tickers)} | {args.start} → sekarang | tiap {args.every} hari")
    predictor = load_predictor()

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    rows, t0 = [], time.time()
    for ti, ticker in enumerate(tickers, 1):
        df = data.fetch_history(ticker, period=args.period)
        if df is None or len(df) < CONTEXT + args.horizon + 30:
            print(f"[{ti}/{len(tickers)}] {ticker}: data kurang, dilewati")
            continue
        ind = analysis.compute_indicators(df)
        close = ind["close"].to_numpy(float)
        positions = sample_dates(ind.index, args.start, args.every)
        if args.dates:
            positions = positions[-args.dates:]

        n_done, t_tick = 0, time.time()
        for i in positions:
            key = (ticker, ind.index[i].normalize())
            if key in done:
                continue
            try:
                pred_ret = predict_one(predictor, ind, i, args.horizon, args.samples)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {ticker} {ind.index[i].date()}: {e}")
                continue
            r = ind.iloc[i]
            sc = analysis.score_from_rows(r, ind.iloc[i - 1])
            rows.append({
                "ticker": ticker,
                "date": ind.index[i].normalize(),
                "kronos": pred_ret,
                "realized": float(close[i + args.horizon] / close[i] - 1),
                "score": float(sc["pct"]),
                "mom20": float(r["ret20d"]) / 100 if pd.notna(r["ret20d"]) else np.nan,
            })
            n_done += 1
            if n_done % 5 == 0:
                pd.DataFrame(rows).to_csv(csv_path, mode="a", index=False,
                                          header=not os.path.exists(csv_path))
                rows = []
                print(f"  {ticker}: {n_done} titik "
                      f"({(time.time() - t_tick) / n_done:.1f}s/ramalan)", flush=True)
        if rows:
            pd.DataFrame(rows).to_csv(csv_path, mode="a", index=False,
                                      header=not os.path.exists(csv_path))
            rows = []
        print(f"[{ti}/{len(tickers)}] {ticker} selesai "
              f"({time.time() - t_tick:.0f}s)", flush=True)

    res = pd.read_csv(csv_path, parse_dates=["date"])
    # satu titik per (ticker, date) — buang duplikat kalau dijalankan ulang
    res = res.drop_duplicates(subset=["ticker", "date"], keep="first")
    print(f"\n{len(res)} observasi, {res['ticker'].nunique()} emiten, "
          f"{res['date'].nunique()} tanggal\n")

    out = {"n_obs": int(len(res)), "n_tickers": int(res["ticker"].nunique()),
           "n_dates": int(res["date"].nunique()),
           "start": str(res["date"].min().date()), "end": str(res["date"].max().date()),
           "horizon": args.horizon}

    print(f"{'sinyal':10} {'mean IC':>8} {'t':>7} {'n tgl':>6}")
    for col in ["kronos", "score", "mom20"]:
        s = summarize(xs_ic(res, col))
        out[col] = s
        print(f"{col:10} {str(s['mean_ic']):>8} {str(s['t']):>7} {s['n']:>6}")

    # baseline contrarian = -momentum (IC-nya kebalikan momentum)
    res["anti_mom"] = -res["mom20"]
    s = summarize(xs_ic(res, "anti_mom"))
    out["anti_mom"] = s
    print(f"{'anti_mom':10} {str(s['mean_ic']):>8} {str(s['t']):>7} {s['n']:>6}")

    out["quintiles"] = {c: risk_metrics(res, c) for c in ["kronos", "score", "anti_mom"]}
    print("\nexcess return per kuintil (vs rata-rata pasar hari itu):")
    for c, q in out["quintiles"].items():
        print(f"  {c:9} {q}")

    with open(os.path.join(args.out, "kronos_eval.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nselesai dalam {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

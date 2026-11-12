"""Harness evaluasi statistik skor SahamBuddy.

Tujuan: mengukur apakah skor benar-benar punya daya beda terhadap imbal hasil
ke depan, SEBELUM mengubah bobot/sinyal.

Yang dihitung:
  1. IC (Information Coefficient) cross-sectional per tanggal — Spearman antara
     skor dan forward return antar-emiten pada hari yang sama.
  2. Monotonisitas desil: rata-rata forward return per desil skor (di-demean
     per tanggal supaya beta pasar tidak mendominasi).
  3. Kinerja per rating + bootstrap CI (resampling per tanggal, bukan per baris,
     karena baris-baris pada tanggal yang sama saling berkorelasi).
  4. Stabilitas per tahun (apakah arahnya konsisten atau cuma efek satu periode).
  5. Efektivitas tiap sinyal/fitur secara terpisah.
  6. Walk-forward: latih model di masa lalu, uji di tahun berikutnya (OOS),
     bandingkan dengan skor tangan saat ini.

Semua angka disimpan ke JSON + ringkasan markdown. Tidak ada look-ahead:
fitur hari i hanya memakai data <= i, target memakai harga i+horizon.

Pakai:
    python research/evaluate.py --limit 12          # uji cepat
    python research/evaluate.py                     # seluruh LQ45
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import analysis, data, saran  # noqa: E402

WARMUP = 210
MIN_TICKERS_PER_DATE = 8
UNIVERSE_NAME = "LQ45_2026"   # komposisi saat evaluasi dijalankan


# ---------------------------------------------------------------- data

def price_cache(ticker: str, period: str) -> pd.DataFrame | None:
    try:
        df = data.fetch_history(ticker, period=period)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {ticker}: {e}")
        return None
    if df is None or len(df) < WARMUP + 120:
        print(f"  ! {ticker}: data terlalu pendek ({0 if df is None else len(df)} baris)")
        return None
    return df


def build_dataset(tickers: list[str], period: str, horizon: int) -> pd.DataFrame:
    rows = []
    for n, t in enumerate(tickers, 1):
        df = price_cache(t, period)
        if df is None:
            continue
        ind = analysis.compute_indicators(df)
        ind["volat20"] = ind["ret1d"].rolling(20).std() / 100
        close = ind["close"].to_numpy(dtype=float)
        fwd = np.full(len(ind), np.nan)
        fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1

        for i in range(WARMUP, len(ind)):
            r = ind.iloc[i]
            if not np.isfinite(fwd[i]):
                continue
            sc = analysis.score_from_rows(r, ind.iloc[i - 1])
            rows.append({
                "date": ind.index[i].normalize(),
                "ticker": t,
                "close": float(r["close"]),
                "score": float(sc["pct"]),
                "rating": sc["rating"],
                "fwd": float(fwd[i]),
                # fitur kontinu (kandidat skor v2)
                "f_close_sma50": float(r["close"] / r["sma50"] - 1) if pd.notna(r["sma50"]) else np.nan,
                "f_close_sma200": float(r["close"] / r["sma200"] - 1) if pd.notna(r["sma200"]) else np.nan,
                "f_sma20_sma50": float(r["sma20"] / r["sma50"] - 1) if pd.notna(r["sma50"]) else np.nan,
                "f_sma50_sma200": float(r["sma50"] / r["sma200"] - 1) if pd.notna(r["sma200"]) else np.nan,
                "f_macd": float(r["macd_hist"] / r["close"]) if pd.notna(r["macd_hist"]) else np.nan,
                "f_rsi": (float(r["rsi14"]) - 60.0) / 20.0 if pd.notna(r["rsi14"]) else np.nan,
                "f_breakout": float(r["close"] / r["high20"] - 1) if pd.notna(r["high20"]) else np.nan,
                "f_volume": float(r["volume"] / r["vol_avg20"] - 1) if pd.notna(r["vol_avg20"]) and r["vol_avg20"] else np.nan,
                "f_mom20": float(r["ret20d"]) / 100 if pd.notna(r["ret20d"]) else np.nan,
                "f_mom1": float(r["ret1d"]) / 100 if pd.notna(r["ret1d"]) else np.nan,
                "f_volat": float(r["volat20"]) if pd.notna(r["volat20"]) else np.nan,
                "f_atr": float(r["atr14"] / r["close"]) if pd.notna(r["atr14"]) else np.nan,
            })
        print(f"  [{n}/{len(tickers)}] {t}: {len(ind)} baris")
        time.sleep(0.25)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("dataset kosong — cek koneksi/universum")
    return df


# ---------------------------------------------------------------- statistik

def xs_ic(df: pd.DataFrame, col: str, ret: str = "fwd",
          min_n: int = MIN_TICKERS_PER_DATE) -> pd.Series:
    """IC per tanggal: Spearman (= Pearson pada rank) antar-emiten."""
    out = {}
    for d, g in df.groupby("date"):
        g = g[[col, ret]].dropna()
        if len(g) < min_n:
            continue
        ra, rb = g[col].rank(), g[ret].rank()
        if ra.std() == 0 or rb.std() == 0:
            continue
        out[d] = float(np.corrcoef(ra, rb)[0, 1])
    return pd.Series(out, dtype=float)


def newey_west_t(values, nlags: int = 0) -> float | None:
    """t-stat rata-rata dengan koreksi Newey-West (kernel Bartlett).

    IC per tanggal tidak independen: label 20 hari saling overlap, jadi t naif
    (mean/std*sqrt(n)) terlalu optimistis. HAC memperhitungkan autokovarians
    sampai `nlags` sehingga error bar-nya lebih jujur.
    """
    x = np.asarray([v for v in values if v == v], dtype=float)
    n = len(x)
    if n < 3:
        return None
    mean = float(x.mean())
    e = x - mean
    gamma0 = float(e @ e) / n
    var = gamma0
    for lag in range(1, min(nlags, n - 1) + 1):
        gamma = float(e[lag:] @ e[:-lag]) / n
        var += 2.0 * (1.0 - lag / (nlags + 1.0)) * gamma
    if var <= 0:
        return None
    se = math.sqrt(var / n)
    return round(mean / se, 2) if se > 0 else None


def ic_summary(ic: pd.Series, nlags: int = 0) -> dict:
    n = len(ic)
    if n == 0:
        return {"n_dates": 0, "mean_ic": None, "ir": None, "t": None,
                "t_hac": None}
    mean = float(ic.mean())
    std = float(ic.std(ddof=1)) if n > 1 else 0.0
    return {
        "n_dates": n,
        "mean_ic": round(mean, 4),
        "ir": round(mean / std, 3) if std else None,
        "t": round(mean / std * math.sqrt(n), 2) if std else None,
        "t_hac": newey_west_t(ic.to_numpy(), nlags) if nlags > 0 else None,
    }


def demean_by_date(df: pd.DataFrame) -> pd.Series:
    return df["fwd"] - df.groupby("date")["fwd"].transform("mean")


def decile_table(df: pd.DataFrame, col: str, q: int = 10) -> list[dict]:
    d = df[["date", col, "fwd"]].dropna().copy()
    d["excess"] = demean_by_date(d)
    d["decile"] = d.groupby("date")[col].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False, duplicates="drop")
        if s.notna().sum() >= q else np.nan)
    d = d.dropna(subset=["decile"])
    g = d.groupby("decile")["excess"]
    return [{"decile": int(k) + 1, "n": int(v.count()),
             "excess_ret_pct": round(float(v.mean()) * 100, 3),
             "win_rate": round(float((v > 0).mean()) * 100, 1)}
            for k, v in g]


def bootstrap_ci(values: np.ndarray, iters: int = 2000, alpha: float = 0.05) -> tuple:
    if len(values) < 5:
        return (None, None)
    rng = np.random.default_rng(42)
    means = [rng.choice(values, len(values), replace=True).mean() for _ in range(iters)]
    return (round(float(np.percentile(means, 100 * alpha / 2)) * 100, 3),
            round(float(np.percentile(means, 100 * (1 - alpha / 2))) * 100, 3))


def rating_table(df: pd.DataFrame) -> list[dict]:
    order = ["STRONG BUY", "BUY", "NETRAL", "SELL", "STRONG SELL"]
    d = df.dropna(subset=["fwd"]).copy()
    # excess = return emiten dikurangi rata-rata emiten lain HARI ITU.
    # Tanpa ini, perbandingan antar-rating mencampur efek pasar: label SELL
    # menumpuk di pasar bearish yang lalu rebound, sehingga terlihat "bagus".
    d["excess"] = demean_by_date(d)
    out = []
    for r in order:
        g = d[d["rating"] == r]
        if g.empty:
            continue
        # bootstrap per TANGGAL: rata-rata harian dulu, baru diresample
        daily = g.groupby("date")["excess"].mean().to_numpy()
        lo, hi = bootstrap_ci(daily)
        out.append({
            "rating": r, "n": int(len(g)), "n_dates": int(len(daily)),
            "mean_pct": round(float(g["fwd"].mean()) * 100, 3),
            "excess_pct": round(float(g["excess"].mean()) * 100, 3),
            "median_pct": round(float(g["fwd"].median()) * 100, 3),
            "win_rate": round(float((g["fwd"] > 0).mean()) * 100, 1),
            "excess_win_rate": round(float((g["excess"] > 0).mean()) * 100, 1),
            "ci95_excess_daily_pct": [lo, hi],
        })
    return out


def time_series_ic(df: pd.DataFrame, col: str, min_n: int = 250) -> dict:
    """IC per emiten (time-series): apakah skor tinggi di emiten yang sama
    mendahului return lebih baik? Ini lensa yang dipakai saat memutuskan
    'beli sekarang atau tidak' pada SATU saham."""
    ics = []
    for _, g in df.groupby("ticker"):
        g = g[[col, "fwd"]].dropna()
        if len(g) < min_n:
            continue
        ra, rb = g[col].rank(), g["fwd"].rank()
        if ra.std() == 0 or rb.std() == 0:
            continue
        ics.append(float(np.corrcoef(ra, rb)[0, 1]))
    if not ics:
        return {"n_tickers": 0, "mean_ic": None, "t": None}
    arr = np.array(ics)
    return {
        "n_tickers": int(len(arr)),
        "mean_ic": round(float(arr.mean()), 4),
        "t": round(float(arr.mean() / arr.std(ddof=1) * math.sqrt(len(arr))), 2)
        if len(arr) > 1 and arr.std(ddof=1) > 0 else None,
    }


def by_year(df: pd.DataFrame) -> list[dict]:
    d = df.dropna(subset=["fwd"]).copy()
    d["year"] = pd.to_datetime(d["date"]).dt.year
    out = []
    for y, g in d.groupby("year"):
        ic = xs_ic(g, "score")
        out.append({
            "year": int(y), "n": int(len(g)),
            "mean_fwd_pct": round(float(g["fwd"].mean()) * 100, 3),
            "win_rate": round(float((g["fwd"] > 0).mean()) * 100, 1),
            "ic": round(float(ic.mean()), 4) if len(ic) else None,
        })
    return out


def signal_table(df: pd.DataFrame, nlags: int = 0) -> list[dict]:
    feats = [c for c in df.columns if c.startswith("f_") and c != "fwd"]
    out = []
    for c in feats + ["score"]:
        ic = xs_ic(df, c)
        s = ic_summary(ic, nlags)
        out.append({"feature": c, **s})
    return sorted(out, key=lambda x: -(abs(x["mean_ic"]) if x["mean_ic"] is not None else 0))


# ---------------------------------------------------------------- walk-forward

def zscore_by_date(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Z-score cross-sectional per tanggal.

    Tanggal dengan satu emiten punya std = NaN; dulu NaN itu merambat ke
    seluruh regresi sehingga semua prediksi jadi NaN (bug walk-forward kosong).
    Sekarang sisa NaN diisi 0 (netral terhadap rata-rata pasar hari itu).
    """
    out = pd.DataFrame(index=df.index)
    for c in cols:
        g = df.groupby("date")[c]
        mu = g.transform("mean")
        sd = g.transform("std")
        out[c] = ((df[c] - mu) / sd.replace(0, np.nan)).fillna(0.0)
    return out.clip(-3, 3)


def rank_target(df: pd.DataFrame) -> pd.Series:
    """Target tahan outlier: peringkat cross-sectional diskalakan ke [-0.5, 0.5]."""
    return df.groupby("date")["fwd"].rank(pct=True) - 0.5


LAM_REL_GRID = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0)


def ridge_fit(X: np.ndarray, y: np.ndarray, lam_rel: float) -> np.ndarray:
    """Ridge dengan penalti relatif terhadap jumlah baris latih.

    `lam_rel=0,1` berarti penalti efektif 0,1 x N (bobot menyusut ~9%).
    Intersep tidak dihukum. Penalti statis `lam=10` pada 10 ribu baris membuat
    regularisasinya praktis nol, sehingga "ridge" hanya nama.
    """
    n, p = X.shape
    pen = np.eye(p) * (max(0.0, lam_rel) * n)
    pen[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + pen, X.T @ y)


def choose_lam_cv(train: pd.DataFrame, feats: list[str], horizon: int,
                  embargo_days: int = 0,
                  grid: tuple[float, ...] = LAM_REL_GRID,
                  n_folds: int = 4) -> dict:
    """Pilih `lam_rel` lewat CV deret waktu DI DALAM train (bukan angka statis).

    Fold memakai blok tanggal berurutan (expanding window); label latih yang
    bisa menyentuh fold uji dipurge dengan konversi horizon bursa -> kalender
    yang sama seperti `purged_split`. Metrik pemilihan: rata-rata IC
    cross-sectional di fold uji. Seri -> lam terbesar (lebih konservatif).

    Return dict: `lam_rel` terpilih, `cv_ic` per kandidat, `folds` valid.
    """
    dates = pd.to_datetime(train["date"])
    uniq = np.sort(pd.unique(dates))
    if len(uniq) < 60 or len(train) < 500:
        return {"lam_rel": 0.1, "cv_ic": {}, "folds": 0,
                "note": "train terlalu pendek untuk CV; lam default 0,10"}
    horizon_days = int(math.ceil(horizon * 7 / 5)) + 2 + max(0, embargo_days)
    chunks = np.array_split(uniq, n_folds + 1)
    scores: dict[float, list[float]] = {g: [] for g in grid}
    for val in chunks[1:]:
        if len(val) < 5:
            continue
        start, end = pd.Timestamp(val[0]), pd.Timestamp(val[-1])
        cutoff = start - pd.Timedelta(days=horizon_days)
        tr = train[dates < cutoff]
        te = train[(dates >= start) & (dates <= end)]
        if len(tr) < 500 or len(te) < 50:
            continue
        Xtr = np.column_stack([np.ones(len(tr)),
                              zscore_by_date(tr, feats).to_numpy()])
        Xte = np.column_stack([np.ones(len(te)),
                              zscore_by_date(te, feats).to_numpy()])
        ytr = rank_target(tr).to_numpy()
        for g in grid:
            w = ridge_fit(Xtr, ytr, g)
            ic = xs_ic(te.assign(_pred=Xte @ w), "_pred")
            if len(ic):
                scores[g].append(float(ic.mean()))
    table = {g: (round(float(np.mean(v)), 4) if v else None)
             for g, v in scores.items() if v}
    if not table:
        return {"lam_rel": 0.1, "cv_ic": {}, "folds": 0,
                "note": "CV tidak menghasilkan fold valid; lam default 0,10"}
    best = max(table, key=lambda g: (table[g], g))
    return {"lam_rel": float(best),
            "cv_ic": {f"{g:g}": v for g, v in table.items()},
            "folds": len(scores[best]), "n_candidates": len(grid)}


def purged_split(df: pd.DataFrame, year: int, horizon: int,
                 embargo_days: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pisahkan train/test dan buang label latih yang overlap ke periode uji.

    Label pada tanggal t memakai harga t+horizon hari bursa. Sampel latih
    dibuang bila jendela labelnya belum selesai sebelum 1 Januari tahun uji.
    Horizon bursa dikonversi ke kalender (7/5 + 2 hari); `embargo_days`
    menambah jeda eksplisit di luar itu.
    """
    test_start = pd.Timestamp(f"{year}-01-01")
    horizon_days = int(math.ceil(horizon * 7 / 5)) + 2
    cutoff = test_start - pd.Timedelta(days=horizon_days + max(0, embargo_days))
    dates = pd.to_datetime(df["date"])
    train = df[dates < cutoff]
    test = df[dates.dt.year == year]
    return train, test


def walk_forward(df: pd.DataFrame, feats: list[str], horizon: int,
                 test_years: list[int], embargo_days: int = 0):
    """Latih di masa lalu, uji tahun berikutnya.

    Return (per-tahun, desil OOS, prediksi OOS). Desil OOS = prediksi model yang
    benar-benar out-of-sample digabung dari semua tahun uji, lalu diurutkan per
    tanggal. Ini dasar kalibrasi: kalau desilnya monoton, prediksi bisa
    diterjemahkan jadi ekspektasi imbal hasil.
    """
    d = df.dropna(subset=feats + ["fwd", "score"]).copy()
    d["year"] = pd.to_datetime(d["date"]).dt.year
    d["target"] = rank_target(d)
    results, oos = [], []
    for y in test_years:
        train, test = purged_split(d, y, horizon, embargo_days)
        if len(train) < 2000 or len(test) < 200:
            results.append({"year": y, "skipped": True, "n_train": len(train), "n_test": len(test)})
            continue

        Xtr = zscore_by_date(train, feats)
        Xte = zscore_by_date(test, feats)
        ytr = train["target"].to_numpy()

        cv = choose_lam_cv(train, feats, horizon, embargo_days)
        lam = cv["lam_rel"]
        X = np.column_stack([np.ones(len(train)), Xtr.to_numpy()])
        w = ridge_fit(X, ytr, lam)

        Xt = np.column_stack([np.ones(len(test)), Xte.to_numpy()])
        pred = Xt @ w
        # Baseline anti-overfit: bobot sama untuk semua faktor (rata-rata z-score).
        eq = Xte.to_numpy().mean(axis=1)
        test = test.assign(pred=pred, eq=eq)
        ic_model = xs_ic(test, "pred")
        ic_score = xs_ic(test, "score")
        ic_eq = xs_ic(test, "eq")

        # kombinasi sederhana: skor lama + model (rata-rata z-score)
        z = pd.Series(pred, index=test.index)
        test = test.assign(blend=z.rank(pct=True) + test["score"].rank(pct=True))
        ic_blend = xs_ic(test, "blend")

        oos.append(test[["date", "ticker", "pred", "fwd"]])
        results.append({
            "year": y, "skipped": False,
            "n_train": int(len(train)), "n_test": int(len(test)),
            "embargo_days": int(embargo_days),
            "train_last": str(pd.to_datetime(train["date"]).max().date()),
            "ic_model": round(float(ic_model.mean()), 4) if len(ic_model) else None,
            "ic_score": round(float(ic_score.mean()), 4) if len(ic_score) else None,
            "ic_eq": round(float(ic_eq.mean()), 4) if len(ic_eq) else None,
            "ic_blend": round(float(ic_blend.mean()), 4) if len(ic_blend) else None,
            "lam_rel": lam,
            "lam_cv": cv,
            "weights": {f: round(float(v), 4) for f, v in zip(feats, w[1:], strict=True)},
            "intercept": round(float(w[0]), 4),
        })

    deciles, od = [], None
    if oos:
        od = pd.concat(oos, ignore_index=True)
        od["excess"] = od["fwd"] - od.groupby("date")["fwd"].transform("mean")
        od["decile"] = od.groupby("date")["pred"].transform(
            lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop")
            if s.notna().sum() >= 5 else np.nan)
        od = od.dropna(subset=["decile"])
        for k, g in od.groupby("decile"):
            deciles.append({
                "quintile": int(k) + 1, "n": int(len(g)),
                "excess_pct": round(float(g["excess"].mean()) * 100, 3),
                "win_rate": round(float((g["excess"] > 0).mean()) * 100, 1),
            })
    return results, deciles, od



# ---------------------------------------------------------------- laporan

def fmt_row(cells, widths):
    return "".join(str(c).ljust(w) for c, w in zip(cells, widths, strict=True))


def render_md(res: dict) -> str:
    L = []
    L.append("# Laporan Evaluasi Skor SahamBuddy\n")
    L.append(f"- Universe: **{res['meta']['n_tickers']} emiten**, "
             f"**{res['meta']['n_rows']:,}** observasi, "
             f"periode {res['meta']['start']} → {res['meta']['end']}")
    L.append(f"- Horizon: **{res['meta']['horizon']} hari bursa**; "
             f"warmup {WARMUP} baris; data {res['meta']['period']}")
    L.append("")

    L.append("## 1. IC cross-sectional (skor vs forward return)\n")
    s = res["ic_score"]
    L.append("| metrik | nilai |\n|---|---|")
    L.append(f"| rata-rata IC | {s['mean_ic']} |")
    L.append(f"| IR (mean/std) | {s['ir']} |")
    L.append(f"| t-stat (naif) | {s['t']} |")
    L.append(f"| t-stat HAC (Newey-West) | {s.get('t_hac')} |")
    L.append(f"| jumlah tanggal | {s['n_dates']} |")
    L.append("")
    L.append("> Aturan kasar: |IC| < 0,03 di saham = lemah; 0,03–0,05 layak; "
             ">0,05 bagus. t > 2 baru layak disebut beda dari nol.\n")
    L.append("> t naif menganggap tiap tanggal independen; label 20 hari yang "
             "overlap membuatnya terlalu optimistis. **t HAC** (lag = horizon) "
             "adalah pembanding yang lebih jujur.\n")

    L.append("## 2. Monotonisitas desil (excess return vs rata-rata pasar hari itu)\n")
    L.append("| desil | n | excess return | win-rate |\n|---|---|---|---|")
    for r in res["deciles"]:
        L.append(f"| D{r['decile']} | {r['n']:,} | {r['excess_ret_pct']:+.3f}% | {r['win_rate']}% |")
    L.append("")

    L.append("## 3. Kinerja per rating (excess = vs rata-rata pasar hari itu)\n")
    L.append("| rating | n | rata-rata | excess | excess win-rate | CI95 excess harian |\n"
             "|---|---|---|---|---|---|")
    for r in res["ratings"]:
        ci = r["ci95_excess_daily_pct"]
        L.append(f"| {r['rating']} | {r['n']:,} | {r['mean_pct']:+.3f}% | "
                 f"**{r['excess_pct']:+.3f}%** | {r['excess_win_rate']}% | "
                 f"[{ci[0]}, {ci[1]}] |")
    L.append("")
    L.append("> Kolom `excess` yang menentukan: tanpa itu, rating SELL terlihat "
             "\"bagus\" hanya karena label itu menumpuk di pasar bearish yang "
             "kemudian rebound bersama-sama.\n")

    L.append("## 4. Stabilitas per tahun\n")
    L.append("| tahun | n | rata-rata fwd | win-rate | IC skor |\n|---|---|---|---|---|")
    for r in res["years"]:
        L.append(f"| {r['year']} | {r['n']:,} | {r['mean_fwd_pct']:+.2f}% | "
                 f"{r['win_rate']}% | {r['ic']} |")
    L.append("")

    L.append("## 5. Kekuatan tiap fitur (IC cross-sectional)\n")
    L.append("| fitur | mean IC | IR | t |\n|---|---|---|---|")
    for r in res["features"]:
        L.append(f"| `{r['feature']}` | {r['mean_ic']} | {r['ir']} | {r['t']} |")
    L.append("")

    L.append("## 6. Walk-forward (latih di masa lalu, uji tahun berikutnya)\n")
    L.append("| tahun uji | n train | n test | IC skor lama | IC bobot sama | IC model | lam | IC gabungan |\n"
             "|---|---|---|---|---|---|---|---|")
    for r in res["walk_forward"]:
        if r.get("skipped"):
            L.append(f"| {r['year']} | {r['n_train']} | {r['n_test']} | — (data kurang) | | | | |")
        else:
            L.append(f"| {r['year']} | {r['n_train']:,} | {r['n_test']:,} | "
                     f"{r['ic_score']} | {r.get('ic_eq')} | {r['ic_model']} | "
                     f"{r.get('lam_rel')} | {r['ic_blend']} |")
    L.append("")

    L.append("## 7. IC time-series per emiten (lensa 'beli sekarang?')\n")
    t = res["ic_timeseries"]
    L.append(f"Rata-rata IC dalam satu emiten sepanjang waktu: **{t['mean_ic']}** "
             f"(t={t['t']}, {t['n_tickers']} emiten).")
    L.append("")

    L.append("## 8. Kalibrasi out-of-sample (kuintil prediksi model)\n")
    L.append("| kuintil | n | excess return | win-rate |\n|---|---|---|---|")
    for r in res.get("oos_quintiles", []):
        L.append(f"| Q{r['quintile']} | {r['n']:,} | {r['excess_pct']:+.3f}% | {r['win_rate']}% |")
    L.append("")

    L.append("## 9. Bobot produksi (rata-rata bobot walk-forward)\n")
    L.append("| fitur | bobot |\n|---|---|")
    for f, v in sorted(res.get("production_weights", {}).items(), key=lambda kv: -abs(kv[1])):
        L.append(f"| `{f}` | {v:+.5f} |")
    L.append("")
    return "\n".join(L)


def _wf_summary(wf: list[dict]) -> dict:
    """Bandingkan model ridge dengan baseline bobot-sama di tahun uji yang sama."""
    ok = [r for r in wf if not r.get("skipped") and r.get("ic_model") is not None]
    if not ok:
        return {"n_years": 0}
    mod = [r["ic_model"] for r in ok]
    eq = [r["ic_eq"] for r in ok if r.get("ic_eq") is not None]
    sc = [r["ic_score"] for r in ok if r.get("ic_score") is not None]
    beats = sum(1 for r in ok
                if r.get("ic_eq") is not None and r["ic_model"] > r["ic_eq"])
    return {
        "n_years": len(ok),
        "ic_model_mean": round(float(np.mean(mod)), 4),
        "ic_eq_mean": round(float(np.mean(eq)), 4) if eq else None,
        "ic_score_mean": round(float(np.mean(sc)), 4) if sc else None,
        "years_model_beats_eq": beats,
        "lam_rel": [r.get("lam_rel") for r in ok],
        "note": ("baseline bobot-sama adalah pembanding anti-overfit; "
                 "kalau model tidak menang, bobot bebas tidak layak dipakai"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="10y")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--embargo", type=int, default=0,
                    help="hari kalender tambahan sebelum tahun uji (purge label)")
    ap.add_argument("--out", default=os.path.join("temp", "research"))
    ap.add_argument("--refresh", action="store_true",
                    help="abaikan cache dataset dan ambil ulang dari Yahoo")
    args = ap.parse_args()

    tickers = list(dict.fromkeys(saran.LQ45))
    if args.limit:
        tickers = tickers[: args.limit]

    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, f"dataset_{args.period}_{len(tickers)}.pkl")
    if os.path.exists(cache) and not args.refresh:
        df = pd.read_pickle(cache)
        print(f"dataset dari cache: {len(df):,} baris")
    else:
        print(f"Mengambil data {len(tickers)} emiten ({args.period})...")
        t0 = time.time()
        df = build_dataset(tickers, args.period, args.horizon)
        df.to_pickle(cache)
        print(f"dataset: {len(df):,} baris, {df['ticker'].nunique()} emiten, "
              f"{df['date'].nunique()} tanggal ({time.time() - t0:.0f}s)")

    feats = [c for c in df.columns if c.startswith("f_")]
    ic_score = xs_ic(df, "score")
    wf, oos_deciles, oos_pred = walk_forward(
        df, feats, args.horizon, [2022, 2023, 2024, 2025, 2026],
        args.embargo)
    # bobot produksi = rata-rata bobot walk-forward (bukan hasil fit ulang
    # memakai seluruh data, supaya angkanya tetap out-of-sample)
    wdf = pd.DataFrame({r["year"]: r["weights"] for r in wf if not r.get("skipped")})
    weights = {f: round(float(wdf.loc[f].mean()), 5) for f in wdf.index}
    res = {
        "meta": {
            "n_tickers": int(df["ticker"].nunique()),
            "n_rows": int(len(df)),
            "start": str(pd.to_datetime(df["date"]).min().date()),
            "end": str(pd.to_datetime(df["date"]).max().date()),
            "horizon": args.horizon,
            "universe": UNIVERSE_NAME,
            "embargo_days": args.embargo,
            "period": args.period,
            "generated": time.strftime("%Y-%m-%d"),
        },
        "ic_score": ic_summary(ic_score, args.horizon),
        "ic_timeseries": time_series_ic(df, "score"),
        "deciles": decile_table(df, "score"),
        "ratings": rating_table(df),
        "years": by_year(df),
        "features": signal_table(df, args.horizon),
        "walk_forward": wf,
        "walk_forward_summary": _wf_summary(wf),
        "oos_quintiles": oos_deciles,
        "production_weights": weights,
        "feature_order": feats,
    }

    with open(os.path.join(args.out, "evaluation.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    # prediksi OOS disimpan agar uji portofolio (research/portfolio.py) memakai
    # angka yang sama persis dengan laporan, tanpa menghitung ulang model.
    if oos_pred is not None:
        oos_pred.to_pickle(os.path.join(args.out, "oos_predictions.pkl"))
        print(f"prediksi OOS: {len(oos_pred):,} baris, "
              f"{oos_pred['date'].nunique()} tanggal")
    md = render_md(res)
    with open(os.path.join(args.out, "evaluation.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print("\n" + md)


if __name__ == "__main__":
    main()

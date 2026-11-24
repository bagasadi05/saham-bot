"""Skor relatif antar-emiten — satu-satunya bagian skor yang lolos uji OOS.

Latar belakang (lihat `temp/research/evaluation.md` & `data/validation.json`):
  - Skor teknikal absolut (0–90) punya IC cross-sectional +0,0115 (t naif 2,68;
    t HAC Newey-West 0,85 — tidak signifikan) dan IC time-series -0,0207
    (t=-2,33). Jadi skor absolut tetap dipakai sebagai BACAAN TREN, bukan
    sinyal beli.
  - Model linier 12 faktor di bawah ini, dilatih walk-forward dengan pemilihan
    regularisasi ridge CV per tahun uji (latih 2017–2021, uji 2022; dst.),
    memberi IC OOS +0,0492 / +0,0490 / +0,0206 / -0,0250 / +0,0584 (rata-rata
    `ic_model_mean=0.0304` vs baseline bobot-sama `-0.0061`; menang 4/5 tahun).
    Kuintil teratas vs terbawah: +0,400% vs -0,204% excess return rata-rata
    per 20 hari bursa.

Artinya: jangan dibaca sebagai "pasti naik". Ini peringkat relatif yang tipis
tapi terukur — berguna untuk memilih di antara kandidat, bukan untuk memprediksi
arah pasar. Bobot di bawah DISINKRONKAN dengan `data/validation.json` hasil
walk-forward CV; jangan diubah tanpa menjalankan ulang `research/evaluate.py`
dan `research/publish.py`.
"""
from __future__ import annotations

import json
import math
import os

import pandas as pd

# Bobot rata-rata walk-forward CV 2022–2026 (`data/validation.json` → weights).
# Nama fitur = kunci internal (tanpa prefiks `f_`).
WEIGHTS = {
    "close_sma50": -0.02034,
    "rsi": 0.0186,
    "sma50_sma200": 0.01126,
    "breakout": -0.01084,
    "mom20": 0.00748,
    "macd": 0.00706,
    "mom1": -0.00668,
    "volat": -0.006,
    "close_sma200": -0.00578,
    "volume": 0.00532,
    "sma20_sma50": 0.00392,
    "atr": 0.00054,
}

# Kalibrasi OOS: excess return rata-rata per kuintil prediksi (20 hari bursa).
# Sumber: `data/validation.json` ("quintiles") & `temp/research/evaluation.md`.
# Ditampilkan sebagai konteks deskriptif, bukan janji.
OOS_QUINTILES = {
    1: {"excess_pct": -0.267, "win_rate": 43.5, "n": 9883},
    2: {"excess_pct": -0.342, "win_rate": 43.8, "n": 9740},
    3: {"excess_pct": -0.105, "win_rate": 45.0, "n": 9524},
    4: {"excess_pct": 0.328, "win_rate": 47.5, "n": 9740},
    5: {"excess_pct": 0.384, "win_rate": 47.2, "n": 9849},
}

MIN_UNIVERSE = 8      # di bawah ini statistik cross-sectional tidak berarti
Z_CLIP = 3.0

VALIDATION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "validation.json",
)
_QUINTILE_CACHE: dict[str, tuple[int | None, dict[int, dict]]] = {}


def factors_from_ind(ind: pd.DataFrame, i: int = -1) -> dict[str, float]:
    """Hitung 12 faktor dari baris indikator. NaN dikembalikan apa adanya."""
    r = ind.iloc[i]

    def ratio(a: str, b: str) -> float:
        va, vb = r.get(a), r.get(b)
        if va is None or vb is None or pd.isna(va) or pd.isna(vb) or float(vb) == 0:
            return float("nan")
        return float(va) / float(vb) - 1.0

    def num(col: str, scale: float = 1.0) -> float:
        v = r.get(col)
        if v is None or pd.isna(v):
            return float("nan")
        return float(v) * scale

    close = num("close")
    return {
        "close_sma50": ratio("close", "sma50"),
        "close_sma200": ratio("close", "sma200"),
        "sma20_sma50": ratio("sma20", "sma50"),
        "sma50_sma200": ratio("sma50", "sma200"),
        "macd": num("macd_hist") / close if close else float("nan"),
        "rsi": (num("rsi14") - 60.0) / 20.0,
        "breakout": ratio("close", "high20"),
        "volume": ratio("volume", "vol_avg20"),
        "mom20": num("ret20d", 0.01),
        "mom1": num("ret1d", 0.01),
        "volat": num("volat20"),
        "atr": num("atr14") / close if close else float("nan"),
    }


def zscore(factors: dict[str, float], stats: dict[str, tuple[float, float]]) -> float:
    """Gabungkan faktor jadi satu skor z (tanpa dimensi)."""
    total = 0.0
    for name, w in WEIGHTS.items():
        x = factors.get(name)
        mu, sd = stats[name]
        if x is None or pd.isna(x) or not math.isfinite(x) or sd <= 0:
            z = 0.0          # data hilang = netral, bukan menghukum
        else:
            z = (x - mu) / sd
        total += w * max(-Z_CLIP, min(Z_CLIP, z))
    return total


def _stats_for(feats: list[dict]) -> dict[str, tuple[float, float]]:
    """Rata-rata & simpangan baku per faktor (samakan std pandas, ddof=1)."""
    stats = {}
    for name in WEIGHTS:
        vals = [f[name] for f in feats if not pd.isna(f.get(name, float("nan")))]
        if len(vals) >= MIN_UNIVERSE:
            mu = sum(vals) / len(vals)
            # Samakan dengan std() pandas (ddof=1) di research/evaluate.py
            var = sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)
            stats[name] = (mu, math.sqrt(var))
        else:
            stats[name] = (0.0, 0.0)   # fitur tak tersedia → diabaikan
    return stats


def _assign_pct_quintile(usable: list[dict], zscores: list[float],
                         z_key: str, pct_key: str, q_key: str) -> None:
    """Isi skor z, persentil & kuintil untuk satu kelompok (global/sektor).

    Penanganan tie adil: nilai z yang sama persis mendapat persentil
    rata-rata grup.
    """
    order = sorted(range(len(usable)), key=lambda idx: zscores[idx])
    n = len(order)
    i = 0
    while i < n:
        j = i + 1
        zi = zscores[order[i]]
        while j < n and abs(zscores[order[j]] - zi) <= 1e-12:
            j += 1
        avg_pos = (i + j - 1) / 2.0
        pct = (avg_pos + 0.5) / n * 100.0
        q = max(1, min(5, int(pct // 20) + 1))
        for k in range(i, j):
            idx = order[k]
            usable[idx].update(
                **{z_key: round(zscores[idx], 5), pct_key: round(pct, 1),
                   q_key: q},
            )
        i = j


def score_universe(items: list[dict]) -> list[dict]:
    """Beri peringkat relatif untuk sekumpulan emiten pada tanggal yang sama.

    items: [{"ticker": str, "ind": DataFrame indikator, "sector": str | None, ...}]
    Mengembalikan list yang sama dengan tambahan:
        rel_z / rel_pct / rel_quintil / rel_ok
            peringkat GLOBAL (tidak berubah sejak evaluasi OOS).
        rel_z_sector / rel_pct_sector / rel_quintil_sector / rel_sector_ok
            peringkat DI DALAM sektornya sendiri — hanya bila item membawa
            "sector" dan kelompok sektor itu berisi >= MIN_UNIVERSE emiten
            yang usable. Kelompok yang terlalu kecil atau tanpa sektor
            mendapat rel_sector_ok=False dan nilai sektor None (jujur:
            bukan memakai angka global yang disamarkan sebagai skor sektor).
        rel_sector      nama sektor yang dipakai (atau None)
        rel_sector_n    jumlah emiten usable dalam kelompok sektor itu
    """
    usable = [it for it in items if hasattr(it.get("ind"), "iloc")]
    if len(usable) < MIN_UNIVERSE:
        for it in items:
            sec = it.get("sector")
            sec = sec.strip() if isinstance(sec, str) else ""
            it.update(rel_z=None, rel_pct=None, rel_quintil=None, rel_ok=False,
                      rel_z_sector=None, rel_pct_sector=None,
                      rel_quintil_sector=None, rel_sector_ok=False,
                      rel_sector=sec or None, rel_sector_n=0)
        return items

    feats = [factors_from_ind(it["ind"]) for it in usable]
    stats = _stats_for(feats)
    zscores = [zscore(f, stats) for f in feats]
    _assign_pct_quintile(usable, zscores, "rel_z", "rel_pct", "rel_quintil")
    for it in usable:
        it["rel_ok"] = True

    # peringkat di dalam sektor (statistik per kelompok, bukan global)
    groups: dict[str, list[int]] = {}
    for pos, it in enumerate(usable):
        sec = it.get("sector")
        sec = sec.strip() if isinstance(sec, str) else ""
        if sec:
            groups.setdefault(sec, []).append(pos)
    for sec, positions in groups.items():
        n_group = len(positions)
        for pos in positions:
            usable[pos]["rel_sector"] = sec
            usable[pos]["rel_sector_n"] = n_group
        if n_group < MIN_UNIVERSE:
            for pos in positions:
                usable[pos].update(rel_z_sector=None, rel_pct_sector=None,
                                   rel_quintil_sector=None,
                                   rel_sector_ok=False)
            continue
        sub_feats = [feats[p] for p in positions]
        sub_stats = _stats_for(sub_feats)
        sub_z = [zscore(f, sub_stats) for f in sub_feats]
        sub_items = [usable[p] for p in positions]
        _assign_pct_quintile(sub_items, sub_z,
                             "rel_z_sector", "rel_pct_sector",
                             "rel_quintil_sector")
        for it in sub_items:
            it["rel_sector_ok"] = True

    # emiten yang datanya tidak layak tetap diberi status jujur
    for it in items:
        if not it.get("rel_ok"):
            sec = it.get("sector")
            sec = sec.strip() if isinstance(sec, str) else ""
            it.update(rel_z=None, rel_pct=None, rel_quintil=None, rel_ok=False,
                      rel_z_sector=None, rel_pct_sector=None,
                      rel_quintil_sector=None, rel_sector_ok=False,
                      rel_sector=sec or None, rel_sector_n=0)
        elif not it.get("rel_sector"):
            it.update(rel_z_sector=None, rel_pct_sector=None,
                      rel_quintil_sector=None, rel_sector_ok=False,
                      rel_sector=None, rel_sector_n=0)
    return items


def _finite_float(val) -> float | None:
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load_oos_quintiles(path: str | None = None) -> dict[int, dict]:
    """Muat kalibrasi kuintil OOS (termasuk band IQR P25–P75 jika tersedia).

    Selalu jatuh aman ke `OOS_QUINTILES` bila berkas tidak ada/rusak dan tidak
    pernah mengarang angka P25–P75 bila belum direkam di `validation.json`.
    """
    target = path or VALIDATION_FILE
    base = {k: dict(v) for k, v in OOS_QUINTILES.items()}
    try:
        mtime = os.stat(target).st_mtime_ns
    except OSError:
        return base

    cached = _QUINTILE_CACHE.get(target)
    if cached is not None and cached[0] == mtime:
        return {k: dict(v) for k, v in cached[1].items()}

    try:
        with open(target, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return base

    for row in payload.get("quintiles") or []:
        if not isinstance(row, dict):
            continue
        try:
            q = int(row.get("quintile"))
        except (TypeError, ValueError):
            continue
        if q not in base:
            continue
        for key in ("excess_pct", "win_rate", "median_pct", "p25_pct", "p75_pct", "iqr_pct"):
            num = _finite_float(row.get(key))
            if num is not None:
                base[q][key] = num
        if isinstance(row.get("n"), int):
            base[q]["n"] = row["n"]

    _QUINTILE_CACHE[target] = (mtime, {k: dict(v) for k, v in base.items()})
    return base


def quintile_dispersion(
    quintil: int,
    quintiles: dict[int, dict] | None = None,
) -> dict:
    """Dispersi empiris deskriptif P25–P75 (IQR) untuk kuintil OOS 1..5."""
    src = quintiles if quintiles is not None else load_oos_quintiles()
    cal = src.get(quintil) or OOS_QUINTILES.get(quintil, {})
    p25 = _finite_float(cal.get("p25_pct"))
    p75 = _finite_float(cal.get("p75_pct"))
    iqr = _finite_float(cal.get("iqr_pct"))
    if iqr is None and p25 is not None and p75 is not None:
        iqr = round(p75 - p25, 3)
    has_iqr = p25 is not None and p75 is not None
    return {
        "quintile": quintil,
        "excess_pct": _finite_float(cal.get("excess_pct")),
        "median_pct": _finite_float(cal.get("median_pct")),
        "p25_pct": p25,
        "p75_pct": p75,
        "iqr_pct": iqr,
        "has_iqr": has_iqr,
    }


def summary_line(item: dict, quintiles: dict[int, dict] | None = None) -> str:
    """Satu baris ringkas untuk laporan Telegram (tanpa HTML)."""
    if not item.get("rel_ok"):
        return "peringkat relatif: butuh ≥8 emiten dalam satu pemindaian"
    q = item["rel_quintil"]
    src = quintiles if quintiles is not None else load_oos_quintiles()
    cal = src.get(q) or OOS_QUINTILES.get(q, {})
    disp = quintile_dispersion(q, src)
    iqr_txt = ""
    if disp["has_iqr"]:
        iqr_txt = (f" · sebaran P25–P75: {disp['p25_pct']:+.2f}% s.d. "
                   f"{disp['p75_pct']:+.2f}%")
    return (
        f"peringkat relatif {item['rel_pct']:.0f}/100 (kuintil {q}/5) · "
        f"historis OOS kuintil ini: {cal.get('excess_pct', 0):+.2f}% vs pasar "
        f"per 20 hari{iqr_txt} · peringkat relatif, bukan jaminan return"
    )


def check_quintile_monotonicity(quintiles: dict[int, dict] | None = None) -> dict:
    """Uji monotonisitas peringkat kuintil Q1..Q5 (Spearman rank correlation, F-12)."""
    qs = quintiles if quintiles is not None else load_oos_quintiles()
    def _qval(v: dict) -> float | None:
        for key in ("excess_ret_pct", "excess_pct", "ret_pct", "mean_pct"):
            if v.get(key) is not None:
                return float(v[key])
        return None

    ordered = sorted(
        (int(k), float(val))
        for k, v in (qs or {}).items()
        if isinstance(v, dict) and (val := _qval(v)) is not None
    )
    if len(ordered) < 3:
        return {"monotonic": False, "spearman_rho": None, "n_quintiles": len(ordered)}
    ranks_q = [i + 1 for i in range(len(ordered))]
    vals = [v for _, v in ordered]
    # Rank values (1..n)
    order_idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks_v = [0.0] * len(vals)
    for r, idx in enumerate(order_idx, 1):
        ranks_v[idx] = float(r)
    n = len(vals)
    d2 = sum((rq - rv) ** 2 for rq, rv in zip(ranks_q, ranks_v, strict=True))
    rho = round(1.0 - (6.0 * d2) / (n * (n * n - 1)), 4)
    strictly_mono = all(vals[i] <= vals[i + 1] for i in range(n - 1))
    return {
        "monotonic": strictly_mono,
        "spearman_rho": rho,
        "n_quintiles": n,
    }

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
    "close_sma50": -0.01766,
    "rsi": 0.01680,
    "sma50_sma200": 0.01076,
    "breakout": -0.01032,
    "mom20": 0.00728,
    "volat": -0.00692,
    "mom1": -0.00670,
    "macd": 0.00662,
    "volume": 0.00554,
    "close_sma200": -0.00526,
    "sma20_sma50": 0.00218,
    "atr": 0.00122,
}

# Kalibrasi OOS: excess return rata-rata per kuintil prediksi (20 hari bursa).
# Sumber: `data/validation.json` ("quintiles") & `temp/research/evaluation.md`.
# Ditampilkan sebagai konteks deskriptif, bukan janji.
OOS_QUINTILES = {
    1: {"excess_pct": -0.204, "win_rate": 44.0, "n": 9775},
    2: {"excess_pct": -0.391, "win_rate": 43.3, "n": 9632},
    3: {"excess_pct": -0.100, "win_rate": 44.9, "n": 9416},
    4: {"excess_pct": 0.292, "win_rate": 47.2, "n": 9632},
    5: {"excess_pct": 0.400, "win_rate": 47.2, "n": 9741},
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


def score_universe(items: list[dict]) -> list[dict]:
    """Beri peringkat relatif untuk sekumpulan emiten pada tanggal yang sama.

    items: [{"ticker": str, "ind": DataFrame indikator, ...}]
    Mengembalikan list yang sama dengan tambahan:
        rel_z       skor mentah (semakin tinggi semakin baik)
        rel_pct     persentil 0–100 di dalam universe
        rel_quintil 1–5 (5 = paling menarik menurut model)
        rel_ok      False kalau universe terlalu kecil / data tak cukup
    """
    usable = [it for it in items if hasattr(it.get("ind"), "iloc")]
    if len(usable) < MIN_UNIVERSE:
        for it in items:
            it.update(rel_z=None, rel_pct=None, rel_quintil=None, rel_ok=False)
        return items

    feats = [factors_from_ind(it["ind"]) for it in usable]
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

    zscores = [zscore(f, stats) for f in feats]
    order = sorted(range(len(usable)), key=lambda idx: zscores[idx])
    n = len(order)
    # Penanganan tie adil: nilai z yang sama persis mendapat persentil rata-rata grup
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
                rel_z=round(zscores[idx], 5),
                rel_pct=round(pct, 1),
                rel_quintil=q,
                rel_ok=True,
            )
        i = j
    # emiten yang datanya tidak layak tetap diberi status jujur
    for it in items:
        if not it.get("rel_ok"):
            it.update(rel_z=None, rel_pct=None, rel_quintil=None, rel_ok=False)
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

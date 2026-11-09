"""Skor relatif antar-emiten — satu-satunya bagian skor yang lolos uji OOS.

Latar belakang (lihat research/evaluation.md):
  - Skor teknikal absolut (0–90) punya IC cross-sectional +0,0115 (praktis nol)
    dan IC time-series -0,0207 (di dalam satu emiten justru sedikit berlawanan
    arah). Jadi skor itu tetap dipakai sebagai BACAAN TREN, bukan sinyal beli.
  - Model linier 12 faktor di bawah ini, dilatih walk-forward (latih 2017–2021,
    uji 2022; latih s.d. 2022, uji 2023; dst.), memberi IC OOS +0,047 / +0,048
    / +0,025 / -0,026 / +0,063 (rata-rata +0,031), dengan bobot yang stabil
    antar tahun. Kuintil teratas vs terbawah: +0,42% vs -0,24% excess return
    per 20 hari.

Artinya: jangan dibaca sebagai "pasti naik". Ini peringkat relatif yang tipis
tapi terukur — berguna untuk memilih di antara kandidat, bukan untuk memprediksi
arah pasar. Bobot di bawah DIBEKUKAN dari hasil walk-forward; jangan diubah
tanpa menjalankan ulang research/evaluate.py.
"""
from __future__ import annotations

import math

import pandas as pd

# Bobot rata-rata walk-forward 2022–2026 (research/evaluate.py → production_weights).
# Nama fitur = kunci internal; lihat FEATURES untuk cara menghitungnya.
WEIGHTS = {
    "close_sma50": -0.02392,
    "rsi": 0.01904,
    "breakout": -0.01122,
    "sma50_sma200": 0.01050,
    "mom20": 0.00890,
    "macd": 0.00776,
    "volat": -0.00738,
    "mom1": -0.00652,
    "volume": 0.00574,
    "sma20_sma50": 0.00524,
    "close_sma200": -0.00468,
    "atr": 0.00118,
}

# Kalibrasi OOS: excess return rata-rata per kuintil prediksi (20 hari bursa).
# Sumber: research/evaluation.md bagian 8. Ditampilkan sebagai konteks, bukan janji.
OOS_QUINTILES = {
    1: {"excess_pct": -0.242, "win_rate": 43.5, "n": 9775},
    2: {"excess_pct": -0.416, "win_rate": 43.6, "n": 9632},
    3: {"excess_pct": -0.090, "win_rate": 45.0, "n": 9416},
    4: {"excess_pct": 0.320, "win_rate": 47.1, "n": 9632},
    5: {"excess_pct": 0.424, "win_rate": 47.4, "n": 9741},
}

MIN_UNIVERSE = 8      # di bawah ini statistik cross-sectional tidak berarti
Z_CLIP = 3.0


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

    feats = {it["ticker"]: factors_from_ind(it["ind"]) for it in usable}
    stats = {}
    for name in WEIGHTS:
        vals = [f[name] for f in feats.values() if not pd.isna(f.get(name, float("nan")))]
        if len(vals) >= MIN_UNIVERSE:
            mu = sum(vals) / len(vals)
            var = sum((v - mu) ** 2 for v in vals) / len(vals)
            stats[name] = (mu, math.sqrt(var))
        else:
            stats[name] = (0.0, 0.0)   # fitur tak tersedia → diabaikan

    ranked = sorted(usable, key=lambda it: zscore(feats[it["ticker"]], stats))
    n = len(ranked)
    for pos, it in enumerate(ranked):
        z = zscore(feats[it["ticker"]], stats)
        pct = (pos + 0.5) / n * 100
        it.update(
            rel_z=round(z, 5),
            rel_pct=round(pct, 1),
            rel_quintil=max(1, min(5, int(pct // 20) + 1)),
            rel_ok=True,
        )
    # emiten yang datanya tidak layak tetap diberi status jujur
    for it in items:
        if not it.get("rel_ok"):
            it.update(rel_z=None, rel_pct=None, rel_quintil=None, rel_ok=False)
    return items


def summary_line(item: dict) -> str:
    """Satu baris ringkas untuk laporan Telegram (tanpa HTML)."""
    if not item.get("rel_ok"):
        return "peringkat relatif: butuh ≥8 emiten dalam satu pemindaian"
    q = item["rel_quintil"]
    cal = OOS_QUINTILES.get(q, {})
    return (f"peringkat relatif {item['rel_pct']:.0f}/100 (kuintil {q}/5) · "
            f"historis OOS kuintil ini: {cal.get('excess_pct', 0):+.2f}% vs pasar "
            f"per 20 hari")

"""Ringkasan portofolio ASLI (uang sungguhan) — bukan paper trading.

Modul ini menerima posisi dari `store.real_positions()` + harga berjalan, lalu
menghitung untung/rugi, bobot, konsentrasi sektor, dan risiko bila SL tersentuh.

Dua hal yang sengaja dibuat eksplisit:

  - **SL dihitung di harga sekarang** (2× ATR14 di bawah harga berjalan), bukan
    SL saat membeli. Artinya angka risiko menjawab "kalau berhenti hari ini",
    dan boleh berbeda dari rencana awal — itu wajar, bukan bug.
  - **Harga tidak tersedia tidak diisi nol.** Posisi tanpa harga tetap
    ditampilkan dengan PnL kosong dan dikeluarkan dari total; jumlahnya
    dilaporkan supaya total tidak terlihat lebih kecil diam-diam.
"""
from __future__ import annotations

import pandas as pd

STOP_ATR = 2.0          # sama dengan saran.stop_price(): 2 x ATR
CONC_POS = 35.0         # satu posisi > 35% nilai = konsentrasi tinggi
CONC_SECTOR = 50.0      # satu sektor > 50% nilai = konsentrasi tinggi


def atr14(df: pd.DataFrame | None) -> float | None:
    """ATR 14 sederhana (rata-rata true range) dari OHLC harian."""
    if df is None or len(df) < 15:
        return None
    try:
        h = df["high"].astype(float)
        low = df["low"].astype(float)
        c = df["close"].astype(float)
    except (KeyError, TypeError, ValueError):
        return None
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()],
                   axis=1).max(axis=1)
    val = tr.rolling(14).mean().iloc[-1]
    if pd.isna(val) or float(val) <= 0:
        return None
    return float(val)


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def summarize(positions: list[dict], quotes: dict[str, dict]) -> dict:
    """Hitung PnL, bobot, konsentrasi sektor, dan risiko SL.

    `quotes[ticker]` = {"close": float|None, "atr": float|None, "stale": bool}.
    """
    rows: list[dict] = []
    value_sum = 0.0
    cost_sum = 0.0
    risk_sum = 0.0
    risk_known = False
    unpriced = 0

    for p in positions:
        t = (p.get("ticker") or "").upper()
        lots = int(p.get("lots") or 0)
        avg = float(p.get("avg_price") or 0.0)
        shares = lots * 100
        cost = avg * shares
        q = quotes.get(t) or {}
        close = q.get("close")
        atr = q.get("atr")
        row = {"ticker": t, "lots": lots, "shares": shares, "avg_price": avg,
               "sector": p.get("sector"), "close": close, "priced": False,
               "value": None, "cost": round(cost), "pnl": None,
               "pnl_pct": None, "weight_pct": None, "sl": None, "risk": None,
               "stale": bool(q.get("stale"))}
        cost_sum += cost
        if close and close > 0:
            value = float(close) * shares
            row.update({
                "priced": True, "close": round(float(close), 2),
                "value": round(value),
                "pnl": round(value - cost),
                "pnl_pct": round((float(close) / avg - 1) * 100, 2) if avg else None,
            })
            value_sum += value
            if atr:
                sl = round(float(close) - STOP_ATR * float(atr), 2)
                row["sl"] = sl
                row["risk"] = round(max(float(close) - sl, 0.0) * shares)
                risk_sum += row["risk"]
                risk_known = True
        else:
            unpriced += 1
        rows.append(row)

    for r in rows:
        if r["priced"] and value_sum:
            r["weight_pct"] = _pct(r["value"], value_sum)

    sectors: dict[str, float] = {}
    for r in rows:
        if r["priced"]:
            key = r.get("sector") or "tidak diketahui"
            sectors[key] = sectors.get(key, 0.0) + (r["value"] or 0.0)
    sector_rows = [{"sector": s, "value": round(v),
                    "pct": _pct(v, value_sum) or 0.0}
                   for s, v in sectors.items()]
    sector_rows.sort(key=lambda x: -x["value"])

    warnings: list[str] = []
    for r in rows:
        if r["weight_pct"] and r["weight_pct"] > CONC_POS:
            warnings.append(f"{r['ticker']} {r['weight_pct']:.0f}% dari "
                            f"portofolio — konsentrasi tinggi")
    for s in sector_rows:
        if s["pct"] > CONC_SECTOR:
            warnings.append(f"sektor {s['sector']} {s['pct']:.0f}% — "
                            "terlalu bergantung satu sektor")

    pnl = value_sum - cost_sum
    return {
        "rows": rows,
        "n_positions": len(rows),
        "value": round(value_sum),
        "cost": round(cost_sum),
        "pnl": round(pnl),
        "pnl_pct": _pct(pnl, cost_sum),
        "risk": round(risk_sum) if risk_known else None,
        "risk_pct": (_pct(risk_sum, value_sum) if risk_known else None),
        "sectors": sector_rows,
        "unpriced": unpriced,
        "warnings": warnings,
    }

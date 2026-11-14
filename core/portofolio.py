"""Ringkasan portofolio ASLI (uang sungguhan) — bukan paper trading.

Modul ini menerima posisi dari `store.real_positions()` + harga berjalan (serta
opsional `open_execs` dari `store.open_executions_by_ticker()`), lalu menghitung
untung/rugi, bobot, konsentrasi sektor, risiko SL awal (1× ATR atau SL tersimpan
dari eksekusi), Chandelier trailing stop, time-stop horizontal, dan risiko ARB.

Hal yang sengaja dibuat eksplisit:

  - **SL awal mengutamakan SL eksekusi yang tercatat**; jika belum tercatat,
    memakai 1× ATR14 di bawah harga berjalan (`saran.stop_price()`, dibulatkan ke
    fraksi tick BEI). Chandelier trailing stop dihitung terpisah agar jejak
    SL awal vs trailing selalu jelas.
  - **Harga atau tanggal masuk tidak tersedia tidak diisi nol.** Posisi tanpa
    harga tetap ditampilkan dengan PnL kosong dan dikeluarkan dari total.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from core import saran

STOP_ATR = 1.0          # selaras dengan saran.stop_price(): 1 x ATR (tick-rounded)
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


def _pos_float(val) -> float | None:
    try:
        x = float(val)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def summarize(
    positions: list[dict],
    quotes: dict[str, dict],
    *,
    open_execs: dict[str, dict] | None = None,
    today: date | None = None,
) -> dict:
    """Hitung PnL, bobot, sektor, SL (eksekusi vs 1×ATR), trailing, & time-stop.

    `quotes[ticker]` = {"close": float|None, "atr": float|None, "stale": bool,
                        opsional "df": DataFrame OHLCV, "prev_close": float}.
    """
    rows: list[dict] = []
    value_sum = 0.0
    cost_sum = 0.0
    risk_sum = 0.0
    risk_known = False
    unpriced = 0
    exit_warnings: list[str] = []

    for p in positions:
        t = (p.get("ticker") or "").upper()
        lots = int(p.get("lots") or 0)
        avg = float(p.get("avg_price") or 0.0)
        shares = lots * 100
        cost = avg * shares
        exec_info = (open_execs or {}).get(t) or {}
        saved_sl = _pos_float(exec_info.get("sl")) or _pos_float(p.get("sl"))
        saved_tp = _pos_float(exec_info.get("tp")) or _pos_float(p.get("tp"))
        entry_date = exec_info.get("trade_date") or p.get("trade_date") or p.get("entry_date")

        q = quotes.get(t) or {}
        close = q.get("close")
        atr = q.get("atr")
        df = q.get("df")
        prev_close = _pos_float(q.get("prev_close"))
        if prev_close is None and isinstance(df, pd.DataFrame) and len(df) >= 2:
            try:
                prev_close = _pos_float(df["close"].iloc[-2])
            except Exception:
                prev_close = None

        row = {
            "ticker": t, "lots": lots, "shares": shares, "avg_price": avg,
            "sector": p.get("sector"), "close": close, "priced": False,
            "value": None, "cost": round(cost), "pnl": None,
            "pnl_pct": None, "pnl_net_pct": None, "weight_pct": None,
            "sl": None, "sl_source": None, "tp": saved_tp, "bep_sl": None,
            "risk": None, "trailing_sl": None, "trailing_hit": False,
            "trailing_source": "tidak_tersedia",
            "entry_date": entry_date, "days_held": None,
            "time_stop_flag": False, "time_stop_action": "TIDAK_TERSEDIA",
            "scale_out": None, "arb_gap_risk": False, "arb_locked": False,
            "arb_warnings": [], "stale": bool(q.get("stale")),
        }
        cost_sum += cost
        if close and close > 0:
            c_val = float(close)
            value = c_val * shares
            pnl_net = round(saran.pnl_pct(avg, c_val), 2) if avg > 0 else None
            row.update({
                "priced": True, "close": round(c_val, 2),
                "value": round(value),
                "pnl": round(value - cost),
                "pnl_pct": round((c_val / avg - 1) * 100, 2) if avg else None,
                "pnl_net_pct": pnl_net,
                "bep_sl": saran.breakeven_stop(avg) if avg > 0 else None,
            })
            value_sum += value

            if saved_sl is not None:
                sl = round(saved_sl, 2)
                sl_src = "eksekusi"
            elif atr and float(atr) > 0:
                sl = float(saran.stop_price(c_val, float(atr)))
                sl_src = "atr"
            else:
                sl = None
                sl_src = None

            if sl is not None:
                row["sl"] = sl
                row["sl_source"] = sl_src
                row["risk"] = round(max(c_val - sl, 0.0) * shares)
                risk_sum += row["risk"]
                risk_known = True

            if saved_tp is not None:
                row["tp"] = round(saved_tp, 2)
            elif atr and float(atr) > 0:
                row["tp"] = float(saran.target_price(c_val, float(atr)))

            if avg > 0:
                atr_v = float(atr) if atr and float(atr) > 0 else avg * 0.03
                row["scale_out"] = saran.scale_out_plan(
                    avg, atr_v, lots, tp_full=row["tp"], close=c_val,
                )

            trail = saran.chandelier_trailing_stop(
                df,
                entry=avg or c_val,
                atr=atr,
                prev_stop=q.get("prev_trailing_sl"),
                since_date=entry_date,
            )
            row["trailing_sl"] = trail["stop"]
            row["trailing_hit"] = bool(trail["hit"])
            row["trailing_source"] = trail["source"]
            if trail["hit"] and trail["stop"] is not None:
                exit_warnings.append(
                    f"{t} tembus Chandelier trailing stop {saran.fmt(trail['stop'])}"
                )

            tstop = saran.time_stop_status(
                entry_date, today=today, pnl_net_pct=pnl_net,
            )
            row["days_held"] = tstop["days_held"]
            row["time_stop_flag"] = bool(tstop["triggered"])
            row["time_stop_action"] = tstop["action"]
            if tstop["triggered"]:
                exit_warnings.append(
                    f"{t} ditahan {tstop['days_held']} hari bursa "
                    f"(>={tstop['max_hold_days']} hari) — {tstop['action']}"
                )

            gap = saran.arb_gap_risk(
                avg or c_val, sl or 0.0, price=c_val, prev_close=prev_close,
            )
            row["arb_gap_risk"] = bool(gap["sl_exceeds_arb"])
            row["arb_locked"] = bool(gap["arb_locked"])
            row["arb_warnings"] = list(gap["warnings"])
            for gw in gap["warnings"]:
                exit_warnings.append(f"{t}: {gw}")
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
    for ew in exit_warnings:
        if ew not in warnings:
            warnings.append(ew)

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

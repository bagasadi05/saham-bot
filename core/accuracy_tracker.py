"""Pelacak akurasi rekomendasi (forward paper-trade & audit H+5 / H+20).

Mencatat setiap rekomendasi yang dikeluarkan beserta zona entry, SL, TP1/TP2,
rezim pasar, dan skornya, lalu mengevaluasi terhadap data OHLCV aktual:
- Apakah zona entry tersentuh dalam masa berlaku (default 5 hari bursa)?
- Bila terisi, apakah TP1 atau SL yang lebih dulu tersentuh dalam horizon (20 hari bursa)?
- Berapa Fill Rate %, Win Rate %, SL Hit Rate %, dan rata-rata return bersih?
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WIB = timezone(timedelta(hours=7))
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_TRACKER_PATH = _DATA_DIR / "recommendation_tracker.json"


def _load_records(path: Path = DEFAULT_TRACKER_PATH) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_records(records: list[dict], path: Path = DEFAULT_TRACKER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record_recommendation(
    ticker: str,
    rec_date: str,
    verdict: str,
    entry_low: float,
    entry_high: float,
    sl: float,
    tp1: float,
    tp2: float | None = None,
    *,
    regime: str | None = None,
    power_score: float | None = None,
    notes: str = "",
    path: Path = DEFAULT_TRACKER_PATH,
) -> dict:
    """Catat satu rekomendasi baru ke tracker (status awal: PENDING)."""
    code = ticker.strip().upper().replace(".JK", "")
    rec = {
        "id": f"{code}_{rec_date}_{int(datetime.now(_WIB).timestamp())}",
        "ticker": code,
        "rec_date": rec_date,
        "verdict": verdict.strip().upper(),
        "entry_low": float(entry_low),
        "entry_high": float(entry_high),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2) if tp2 is not None else None,
        "regime": regime,
        "power_score": power_score,
        "notes": notes,
        "status": "PENDING",
        "outcome": None,
        "filled_date": None,
        "fill_price": None,
        "exit_date": None,
        "exit_price": None,
        "return_pct": None,
        "evaluated_at": None,
    }
    records = _load_records(path)
    records.append(rec)
    _save_records(records, path)
    return rec


def _extract_col(df, name: str):
    for col in df.columns:
        cname = col[0] if isinstance(col, tuple) else col
        if str(cname).lower() == name.lower():
            return df[col]
    return None


def _evaluate_one(
    rec: dict,
    df,
    *,
    entry_expiry_days: int = 5,
    horizon_days: int = 20,
    fee_roundtrip_pct: float = 0.40,
) -> dict:
    if df is None or df.empty:
        return rec
    rec_date = str(rec["rec_date"])[:10]
    dates = [str(idx)[:10] for idx in df.index]
    after_idx = [i for i, d in enumerate(dates) if d > rec_date]
    if not after_idx:
        return rec

    highs = _extract_col(df, "high")
    lows = _extract_col(df, "low")
    closes = _extract_col(df, "close")
    if highs is None or lows is None or closes is None:
        return rec

    entry_high = float(rec["entry_high"])
    entry_low = float(rec["entry_low"])
    sl = float(rec["sl"])
    tp1 = float(rec["tp1"])

    filled_i = None
    fill_px = None
    for i in after_idx[:entry_expiry_days]:
        lo = float(lows.iloc[i])
        hi = float(highs.iloc[i])
        if lo <= entry_high:
            filled_i = i
            fill_px = min(entry_high, max(entry_low, lo)) if hi >= entry_low else entry_high
            break

    now_iso = datetime.now(_WIB).strftime("%Y-%m-%d %H:%M WIB")
    if filled_i is None:
        if len(after_idx) >= entry_expiry_days:
            rec["status"] = "EXPIRED"
            rec["outcome"] = "UNFILLED"
            rec["evaluated_at"] = now_iso
        return rec

    rec["filled_date"] = dates[filled_i]
    rec["fill_price"] = round(fill_px, 2)

    post_bars = [i for i in after_idx if i >= filled_i][:horizon_days]
    for i in post_bars:
        lo = float(lows.iloc[i])
        hi = float(highs.iloc[i])
        # Konservatif: bila SL dan TP1 sama-sama tersentuh di bar yang sama, anggap SL kena dulu
        if lo <= sl:
            gross = (sl - fill_px) / fill_px * 100.0
            rec["status"] = "CLOSED"
            rec["outcome"] = "SL_HIT"
            rec["exit_date"] = dates[i]
            rec["exit_price"] = round(sl, 2)
            rec["return_pct"] = round(gross - fee_roundtrip_pct, 2)
            rec["evaluated_at"] = now_iso
            return rec
        if hi >= tp1:
            gross = (tp1 - fill_px) / fill_px * 100.0
            rec["status"] = "CLOSED"
            rec["outcome"] = "TP1_HIT"
            rec["exit_date"] = dates[i]
            rec["exit_price"] = round(tp1, 2)
            rec["return_pct"] = round(gross - fee_roundtrip_pct, 2)
            rec["evaluated_at"] = now_iso
            return rec

    last_i = post_bars[-1]
    last_close = float(closes.iloc[last_i])
    gross = (last_close - fill_px) / fill_px * 100.0
    rec["return_pct"] = round(gross - fee_roundtrip_pct, 2)
    rec["evaluated_at"] = now_iso
    if len(post_bars) >= horizon_days:
        rec["status"] = "CLOSED"
        rec["outcome"] = "TIME_EXIT"
        rec["exit_date"] = dates[last_i]
        rec["exit_price"] = round(last_close, 2)
    else:
        rec["status"] = "OPEN"
        rec["outcome"] = "OPEN_IN_HORIZON"
    return rec


def evaluate_recommendations(
    fetch_fn=None,
    *,
    entry_expiry_days: int = 5,
    horizon_days: int = 20,
    path: Path = DEFAULT_TRACKER_PATH,
) -> list[dict]:
    """Evaluasi ulang semua rekomendasi yang belum final (PENDING / OPEN)."""
    if fetch_fn is None:
        import core.data as data
        fetch_fn = lambda sym: data.fetch_history(data.normalize_ticker(sym), period="6mo")

    records = _load_records(path)
    cache_df: dict[str, object] = {}
    for idx, rec in enumerate(records):
        if rec.get("status") in ("CLOSED", "EXPIRED"):
            continue
        t = rec["ticker"]
        if t not in cache_df:
            try:
                cache_df[t] = fetch_fn(t)
            except Exception:
                cache_df[t] = None
        records[idx] = _evaluate_one(
            rec,
            cache_df[t],
            entry_expiry_days=entry_expiry_days,
            horizon_days=horizon_days,
        )
    _save_records(records, path)
    return records


def summarize_accuracy(
    records: list[dict] | None = None,
    *,
    path: Path = DEFAULT_TRACKER_PATH,
) -> dict:
    """Hitung metrik akurasi nyata: Fill Rate, Win Rate, SL Rate, & rata-rata return."""
    rows = _load_records(path) if records is None else records
    total = len(rows)
    expired = [r for r in rows if r.get("status") == "EXPIRED"]
    filled = [r for r in rows if r.get("filled_date")]
    closed = [r for r in rows if r.get("status") == "CLOSED"]
    tp1_hits = [r for r in closed if r.get("outcome") == "TP1_HIT"]
    sl_hits = [r for r in closed if r.get("outcome") == "SL_HIT"]
    rets = [float(r["return_pct"]) for r in closed if r.get("return_pct") is not None]

    resolved_entry = len(filled) + len(expired)
    fill_rate = round(len(filled) / resolved_entry * 100.0, 1) if resolved_entry else None
    win_rate = round(len(tp1_hits) / len(closed) * 100.0, 1) if closed else None
    sl_rate = round(len(sl_hits) / len(closed) * 100.0, 1) if closed else None
    avg_ret = round(sum(rets) / len(rets), 2) if rets else None

    by_regime: dict[str, dict] = {}
    for r in rows:
        reg = r.get("regime") or "UNKNOWN"
        bucket = by_regime.setdefault(reg, {"total": 0, "filled": 0, "tp1": 0, "sl": 0})
        bucket["total"] += 1
        if r.get("filled_date"):
            bucket["filled"] += 1
        if r.get("outcome") == "TP1_HIT":
            bucket["tp1"] += 1
        elif r.get("outcome") == "SL_HIT":
            bucket["sl"] += 1

    return {
        "total_records": total,
        "pending": sum(1 for r in rows if r.get("status") == "PENDING"),
        "open": sum(1 for r in rows if r.get("status") == "OPEN"),
        "expired_unfilled": len(expired),
        "filled_count": len(filled),
        "closed_count": len(closed),
        "fill_rate_pct": fill_rate,
        "tp1_hit_count": len(tp1_hits),
        "sl_hit_count": len(sl_hits),
        "win_rate_pct": win_rate,
        "sl_rate_pct": sl_rate,
        "avg_closed_return_pct": avg_ret,
        "by_regime": by_regime,
    }

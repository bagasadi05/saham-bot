"""P2 — perbandingan antar-emiten & konteks pasar (IHSG, ^JKSE via Yahoo).

Semua angka dari sumber yang sama dengan /fundamental & /analisis (Yahoo).
Data yang gagal diambil dilaporkan jujur — tidak ada angka karangan."""
from __future__ import annotations

import time as _time

from core import data, fundamental, powerscore

_MKT_TTL = 6 * 3600      # cache IHSG 6 jam (pembanding tidak perlu real-time)
_MKT_FAIL_TTL = 5 * 60   # gagal: coba lagi 5 menit lagi, jangan dikunci 6 jam
_mkt_cache: dict = {"ts": 0.0, "val": None}


def market_context() -> dict | None:
    """Return IHSG {'ret20d', 'ret1y'} (%) atau None kalau Yahoo gagal."""
    ttl = _MKT_TTL if _mkt_cache["val"] else _MKT_FAIL_TTL
    if _time.time() - _mkt_cache["ts"] < ttl:
        return _mkt_cache["val"]
    df = None
    for _ in range(2):
        try:
            df = data.fetch_history("^JKSE", "1y", raw_symbol=True)
            if df is not None and len(df) >= 25:
                break
        except Exception:
            df = None
    out = None
    if df is not None and len(df) >= 25:
        close = df["close"].reset_index(drop=True)

        def _pct(back: int) -> float | None:
            try:
                return float(close.iloc[-1] / close.iloc[-1 - back] - 1) * 100
            except Exception:
                return None

        out = {"ret20d": _pct(20), "ret1y": _pct(min(245, len(close) - 1))}
    _mkt_cache["ts"] = _time.time()
    _mkt_cache["val"] = out  # gagal juga dicache sebentar agar tidak spam
    return out


def compare_row(ticker: str, res: dict | None, fund: dict | None) -> dict:
    """Bundel angka pembanding satu emiten (semua bisa None = tak tersedia)."""
    out = {"ticker": ticker.upper(),
           "per": None, "pbv": None, "roe": None, "dy": None,
           "ret20d": None, "ret1y": None, "power": None}
    if res:
        snap = res["snap"]
        out["ret20d"] = snap.get("ret20d")
        try:
            close = res["ind"]["close"].reset_index(drop=True)
            out["ret1y"] = float(close.iloc[-1] / close.iloc[0] - 1) * 100
        except Exception:
            out["ret1y"] = None
        out["power"] = powerscore.combine(
            res["score"]["pct"],
            fundamental.score_fundamentals(fund)["pct"] if fund else None,
            None)["pct"]
    if fund:
        roe, dy = fund.get("roe"), fund.get("div_yield")
        out["per"] = fund.get("per")
        out["pbv"] = fund.get("pbv")
        out["roe"] = (roe * 100 if (roe is not None and roe < 3) else roe)
        out["dy"] = (dy * 100 if (dy is not None and dy < 1) else dy)
    return out


def fmt_cell(v, tpl: str, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return (tpl % v) + suffix
    except Exception:
        return "—"


def pbv_verdict(rows: list[dict]) -> str | None:
    """Kalimat fakta dari angka PBV (bukan rekomendasi)."""
    with_pbv = [(r["ticker"], r["pbv"]) for r in rows if r["pbv"] is not None]
    if len(with_pbv) < 2:
        return None
    cheapest = min(with_pbv, key=lambda x: x[1])
    priciest = max(with_pbv, key=lambda x: x[1])
    return (f"Fakta dari angka PBV: paling murah {cheapest[0]} ({cheapest[1]:.2f}), "
            f"paling mahal {priciest[0]} ({priciest[1]:.2f}). "
            f"PBV rendah ≠ pasti lebih baik — baca bersama ROE & tren laba.")

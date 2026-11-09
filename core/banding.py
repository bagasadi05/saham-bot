"""P2 & P9 — perbandingan antar-emiten, konteks pasar (IHSG), & peer sektor.

Semua angka dari sumber yang sama dengan /fundamental & /analisis (Yahoo).
Data yang gagal diambil dilaporkan jujur — tidak ada angka karangan."""
from __future__ import annotations

import time as _time

from core import data, fundamental, powerscore

_MKT_TTL = 6 * 3600      # cache IHSG 6 jam (pembanding tidak perlu real-time)
_MKT_FAIL_TTL = 5 * 60   # gagal: coba lagi 5 menit lagi, jangan dikunci 6 jam
_mkt_cache: dict = {"ts": 0.0, "val": None}

# Kluster emiten likuid BEI untuk pembanding sektor cepat tanpa spam `.info`
SECTOR_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Financial Services", ("BBCA", "BBRI", "BMRI", "BBNI", "BRIS")),
    ("Communication Services", ("TLKM", "ISAT", "EXCL", "TBIG")),
    ("Energy", ("ADRO", "PTBA", "ITMG", "MEDC", "PGAS", "AADI", "PGEO")),
    ("Basic Materials", ("ANTM", "MDKA", "MBMA", "TPIA", "BRPT", "SMGR", "INKP", "AMMN")),
    ("Consumer Defensive", ("ICBP", "INDF", "UNVR", "AMRT", "CPIN", "JPFA", "HMSP", "GGRM")),
    ("Industrials", ("ASII", "UNTR", "JSMR")),
    ("Healthcare", ("KLBF",)),
    ("Consumer Cyclical", ("MAPI", "EMTK")),
    ("Real Estate", ("CTRA",)),
)


def market_context() -> dict | None:
    """Return IHSG {'ret20d', 'ret1y'} (%) atau None kalau Yahoo gagal."""
    age = _time.time() - _mkt_cache["ts"]
    if _mkt_cache["val"] is not None and age < _MKT_TTL:
        return _mkt_cache["val"]
    if _mkt_cache["val"] is None and age < _MKT_FAIL_TTL:
        return None  # kegagalan baru: jangan spam Yahoo tiap request
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


def pick_sector_peers(ticker: str, sector: str | None = None,
                      watchlist: list[str] | None = None,
                      limit: int = 2) -> list[str]:
    """Pilih hingga 2 emiten se-sektor dari watchlist atau kluster BEI."""
    sym = ticker.strip().upper().replace(".JK", "")
    candidates: list[str] = []
    for _sec_name, members in SECTOR_CLUSTERS:
        if sym in members or (sector and _sec_name.lower() in sector.lower()):
            for m in members:
                if m != sym and m not in candidates:
                    candidates.append(m)
    if watchlist:
        wl_clean = [w.strip().upper().replace(".JK", "") for w in watchlist]
        prioritized = [c for c in candidates if c in wl_clean]
        others = [c for c in candidates if c not in wl_clean]
        candidates = prioritized + others
    return candidates[:limit]


def sector_context(ticker: str, sector: str | None = None,
                   watchlist: list[str] | None = None) -> dict | None:
    """[P9] Rata-rata return 20h & 1thn dari 1-2 pembanding se-sektor."""
    peers = pick_sector_peers(ticker, sector, watchlist)
    if not peers:
        return None
    r20s: list[float] = []
    r1ys: list[float] = []
    used: list[str] = []
    for peer in peers:
        try:
            df = data.fetch_history(peer, "1y")
        except Exception:
            df = None
        if df is None or len(df) < 25:
            continue
        close = df["close"].reset_index(drop=True)
        try:
            r20 = float(close.iloc[-1] / close.iloc[-21] - 1) * 100
            r1y = float(close.iloc[-1] / close.iloc[0] - 1) * 100
            r20s.append(r20)
            r1ys.append(r1y)
            used.append(peer)
        except Exception:
            continue
    if not used:
        return None
    return {
        "peers": used,
        "sector": sector,
        "ret20d": sum(r20s) / len(r20s),
        "ret1y": sum(r1ys) / len(r1ys),
    }


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
        out["per"] = fund.get("per")
        out["pbv"] = fund.get("pbv")
        out["roe"] = fundamental.normalize_roe(fund.get("roe"))
        out["dy"] = fundamental.normalize_div_yield(fund.get("div_yield"))
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
            "PBV rendah ≠ pasti lebih baik — baca bersama ROE & tren laba.")

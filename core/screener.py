"""Screener seluruh pasar BEI — sinyal BELI / JUAL / TAHAN yang jujur.

Kenapa batch harian, bukan per-perintah?
  ±950 emiten tidak mungkin dianalisis satu per satu di dalam handler Telegram
  (bisa 20–40 menit). Screener mengunduh harga harian secara borongan
  (`yfinance.download` 80 ticker per batch), menghitung indikator yang sama
  dengan `/analisis`, lalu memberi peringkat relatif lintas pasar memakai model
  12 faktor `core/relatif.py` (satu-satunya bagian skor yang lolos uji OOS).

Batas kejujuran:
  - Sinyal = peringkat statistik tipis (IC OOS rata-rata +0,03), BUKAN ramalan.
  - Hanya teknikal + faktor harga. Fundamental/berita tetap lewat /analisis.
  - Emiten tanpa data cukup dilewati dan jumlahnya dilaporkan, tidak diisi.
  - JUAL berarti "hindari / kurangi kalau punya" — bursa IDX tidak mendukung
    short selling ritel, jadi bukan instruksi short.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
import time

import pandas as pd

from core import analysis, marketcal, relatif, store, universe

log = logging.getLogger("sahambuddy.screener")

PERIOD = os.getenv("FULL_SCAN_PERIOD", "1y")
CHUNK = max(10, int(os.getenv("FULL_SCAN_CHUNK", "80") or 80))
PAUSE = float(os.getenv("FULL_SCAN_PAUSE", "1.0") or 0)
MIN_BARS = 30            # bar harian minimum agar indikator masuk akal
BUY_PCT = 70.0           # persentil relatif minimum untuk kandidat BELI
SELL_PCT = 30.0          # persentil relatif maksimum untuk kandidat JUAL
MAX_CHASE = 15.0         # % kenaikan 20 hari; di atas ini jangan dikejar
RETRIES = 2

SIGNALS = ("BELI", "JUAL", "TAHAN")


# ------------------------------------------------------------------ unduh
def _slice(raw: pd.DataFrame | None, full_symbol: str) -> pd.DataFrame | None:
    """Ambil satu emiten dari hasil `yf.download` (multi-index atau tunggal)."""
    if raw is None or getattr(raw, "empty", True):
        return None
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            level0 = raw.columns.get_level_values(0)
            if full_symbol not in level0:
                return None
            sub = raw[full_symbol].copy()
        else:  # satu ticker: kolom flat
            sub = raw.copy()
    except Exception:
        return None

    sub = sub.rename(columns={c: str(c).lower() for c in sub.columns})
    if any(c not in sub.columns for c in ("open", "high", "low", "close")):
        return None
    if "volume" not in sub.columns:
        sub["volume"] = 0.0
    sub = sub[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce")
    sub = sub.dropna(subset=["close"])
    sub = sub[sub["close"] > 0]
    sub = sub[~sub.index.duplicated(keep="last")].sort_index()
    if len(sub) < MIN_BARS:
        return None
    return sub


def _download_chunk(symbols: list[str], period: str) -> pd.DataFrame | None:
    import yfinance as yf

    full = [f"{s}.JK" for s in symbols]
    return yf.download(full, period=period, interval="1d", group_by="ticker",
                       threads=True, progress=False, auto_adjust=False,
                       timeout=30)


def fetch_frames(symbols: list[str], period: str = PERIOD, chunk: int = CHUNK,
                 pause: float = PAUSE, retries: int = RETRIES,
                 progress=None) -> dict[str, pd.DataFrame]:
    """Unduh harga harian semua emiten secara borongan.

    Return {kode: DataFrame(open/high/low/close/volume)}. Emiten yang gagal
    atau datanya < MIN_BARS tidak dimasukkan — pemanggil melaporkannya.
    """
    frames: dict[str, pd.DataFrame] = {}
    total = len(symbols)
    done = 0
    for start in range(0, total, chunk):
        batch = symbols[start:start + chunk]
        raw = None
        for attempt in range(retries + 1):
            try:
                raw = _download_chunk(batch, period)
                break
            except Exception as exc:  # rate limit / jaringan
                log.warning("batch %d-%d gagal (%s), coba lagi %d/%d",
                            start, start + len(batch), type(exc).__name__,
                            attempt + 1, retries)
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
        if raw is not None:
            for sym in batch:
                sub = _slice(raw, f"{sym}.JK")
                if sub is not None and len(sub) >= MIN_BARS:
                    frames[sym] = sub
        done += len(batch)
        if progress:
            try:
                progress(done, total, len(frames))
            except Exception:
                pass
        if pause > 0 and start + chunk < total:
            time.sleep(pause)
    return frames


# --------------------------------------------------------------- evaluasi
def _f(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def classify(close, sma20, sma50, rsi_v, macd_hist, ret20d,
             rel_pct, rel_quintil, rel_ok, tech_pct) -> tuple[str, str]:
    """Tentukan BELI / JUAL / TAHAN + alasan singkat (tanpa klaim berlebihan)."""
    if sma50 is None or close is None:
        return "TAHAN", "Riwayat harga terlalu pendek — tren belum terbentuk"
    up = (close > sma50
          and (sma20 is None or sma20 > sma50)
          and (macd_hist is None or macd_hist > 0))
    down = (close < sma50
            and (sma20 is None or sma20 < sma50)
            and (macd_hist is None or macd_hist < 0))
    rsi_show = 50.0 if rsi_v is None else rsi_v
    chase = ret20d is not None and ret20d > MAX_CHASE

    if rel_ok and rel_pct is not None:
        pct = f"{rel_pct:.0f}"
        q = f" (kuintil {rel_quintil})" if rel_quintil else ""
        if up and rel_pct >= BUY_PCT and 40 <= rsi_show <= 75 and not chase:
            return ("BELI",
                    f"Tren naik (MA20 > MA50, MACD+), RSI {rsi_show:.0f}, "
                    f"persentil {pct}{q}")
        if down and rel_pct <= SELL_PCT and rsi_show < 50:
            return ("JUAL",
                    f"Tren turun (MA20 < MA50, MACD−), RSI {rsi_show:.0f}, "
                    f"persentil {pct}{q}")
        extra = " — sudah naik tajam, jangan dikejar" if chase and up else ""
        return ("TAHAN", f"Tren/peringkat belum selaras{extra}")

    # universe kecil / data relatif tidak tersedia → pakai skor teknikal
    if tech_pct is None:
        return "TAHAN", "Data indikator belum cukup"
    if up and tech_pct >= 40 and 40 <= rsi_show <= 75 and not chase:
        return "BELI", f"Tren naik, skor teknikal {tech_pct:+.0f}"
    if down and tech_pct <= -40:
        return "JUAL", f"Tren turun, skor teknikal {tech_pct:+.0f}"
    return "TAHAN", "Sinyal teknikal belum selaras"


def evaluate(frames: dict[str, pd.DataFrame],
             names: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Hitung indikator + peringkat relatif untuk semua emiten."""
    items: list[dict] = []
    skipped: list[str] = []
    for sym, df in frames.items():
        try:
            ind = analysis.compute_indicators(df)
            if len(ind) < MIN_BARS:
                skipped.append(sym)
                continue
            items.append({"ticker": sym, "ind": ind, "df": df})
        except Exception:
            log.exception("indikator gagal untuk %s", sym)
            skipped.append(sym)

    if not items:
        return [], skipped

    relatif.score_universe(items)

    rows: list[dict] = []
    for it in items:
        sym, ind = it["ticker"], it["ind"]
        last = ind.iloc[-1]
        try:
            tech_pct = _f(analysis.score_stock(ind).get("pct"))
        except Exception:
            tech_pct = None

        close = _f(last.get("close"))
        sma20 = _f(last.get("sma20"))
        sma50 = _f(last.get("sma50"))
        sma200 = _f(last.get("sma200"))
        rsi_v = _f(last.get("rsi14"))
        hist = _f(last.get("macd_hist"))
        atr = _f(last.get("atr14"))
        ret1d = _f(last.get("ret1d"))
        ret20d = _f(last.get("ret20d"))
        ret5d = None
        try:
            if len(ind) > 5:
                ret5d = _f((ind["close"].iloc[-1] / ind["close"].iloc[-6] - 1) * 100)
        except Exception:
            ret5d = None

        rel_pct = _f(it.get("rel_pct"))
        rel_quintil = it.get("rel_quintil") if it.get("rel_ok") else None
        rel_ok = bool(it.get("rel_ok"))

        signal, reason = classify(close, sma20, sma50, rsi_v, hist, ret20d,
                                  rel_pct, rel_quintil, rel_ok, tech_pct)

        entry = sl = tp = None
        if signal == "BELI" and close:
            entry = round(close, 2)
            if atr and atr > 0:
                sl = round(max(close - 1.0 * atr, 1.0), 2)
                tp = round(close + 2.0 * atr, 2)

        try:
            trade_date = pd.Timestamp(ind.index[-1]).date().isoformat()
        except Exception:
            trade_date = dt.date.today().isoformat()

        rows.append({
            "ticker": sym,
            "name": (names or {}).get(sym, universe.name_of(sym)),
            "board": universe.board_of(sym),
            "trade_date": trade_date,
            "price": round(close, 2) if close is not None else None,
            "ret1d": round(ret1d, 2) if ret1d is not None else None,
            "ret5d": round(ret5d, 2) if ret5d is not None else None,
            "ret20d": round(ret20d, 2) if ret20d is not None else None,
            "rsi": round(rsi_v, 1) if rsi_v is not None else None,
            "macd_hist": round(hist, 3) if hist is not None else None,
            "sma20": round(sma20, 2) if sma20 is not None else None,
            "sma50": round(sma50, 2) if sma50 is not None else None,
            "sma200": round(sma200, 2) if sma200 is not None else None,
            "atr": round(atr, 2) if atr is not None else None,
            "tech_score": round(tech_pct, 1) if tech_pct is not None else None,
            "rel_pct": round(rel_pct, 1) if rel_pct is not None else None,
            "signal": signal,
            "reason": reason,
            "entry": entry,
            "sl": sl,
            "tp": tp,
        })
    return rows, skipped


# ------------------------------------------------------------------- scan
def _today_wib() -> str:
    return marketcal.now_wib().date().isoformat()


def scan(force: bool = False, downloader=None,
         symbols: list[str] | None = None, progress=None) -> dict:
    """Jalankan (atau ambil cache) pemindaian seluruh pasar.

    `downloader` bisa diinjeksi untuk tes offline: callable(symbols) -> frames.
    Return ringkasan: cached, counts, meta, skipped.
    """
    meta_lama = store.market_scan_meta()
    counts = store.market_scan_counts()
    if (not force and counts and meta_lama.get("scanned_at", "")[:10]
            == _today_wib()):
        return {"cached": True, "counts": counts, "meta": meta_lama,
                "skipped": []}

    syms = symbols if symbols is not None else universe.codes()
    if not syms:
        raise RuntimeError("Daftar emiten kosong — jalankan tools/build_universe.py")

    t0 = time.time()
    frames = (downloader(syms) if downloader
              else fetch_frames(syms, progress=progress))
    rows, skipped = evaluate(frames)
    if not rows:
        raise RuntimeError("Tidak ada emiten dengan data harga yang cukup")

    trade_dates = [r["trade_date"] for r in rows if r.get("trade_date")]
    trade_date = max(trade_dates) if trade_dates else _today_wib()
    counts = {}
    for r in rows:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1

    umeta = universe.meta()
    meta = {
        "scanned_at": marketcal.now_wib().strftime("%Y-%m-%dT%H:%M:%S"),
        "trade_date": trade_date,
        "universe": len(syms),
        "ok": len(rows),
        "skipped": len(skipped),
        "failed": max(0, len(syms) - len(rows)),
        "elapsed_sec": round(time.time() - t0, 1),
        "universe_source": umeta.get("source", ""),
        "universe_snapshot": umeta.get("fetched_at", ""),
        "buy": counts.get("BELI", 0),
        "sell": counts.get("JUAL", 0),
        "hold": counts.get("TAHAN", 0),
    }
    store.save_market_scan(rows, meta)
    return {"cached": False, "counts": counts, "meta": meta,
            "skipped": skipped}

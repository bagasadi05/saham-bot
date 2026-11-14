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
  - Emiten tanpa data cukup, OHLC cacat, atau bar terakhir tertinggal terlalu
    lama (suspen/saham tidur) dilewati dan jumlahnya dilaporkan, tidak diisi.
  - Level entry/SL/TP dibulatkan ke fraksi tick BEI via `pricing.round_tick`.
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

from core import analysis, data as datamod, data_quality, marketcal, pricing, relatif, scorecard, store, universe

log = logging.getLogger("sahambuddy.screener")

PERIOD = os.getenv("FULL_SCAN_PERIOD", "1y")
CHUNK = max(10, int(os.getenv("FULL_SCAN_CHUNK", "80") or 80))
PAUSE = float(os.getenv("FULL_SCAN_PAUSE", "1.0") or 0)
MIN_BARS = 30            # bar harian minimum agar indikator masuk akal
MAX_STALE_DAYS = max(
    1,
    int(os.getenv("FULL_SCAN_MAX_STALE_DAYS", str(data_quality.STALE_TRADING_DAYS))
        or data_quality.STALE_TRADING_DAYS),
)
MAX_ZERO_VOL_RECENT = 5  # >=5 dari 20 bar terakhir volume 0 -> tahan dari BELI
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
    return datamod.download_yf_with_safe_repair(
        yf.download, full, period=period, interval="1d", group_by="ticker",
        threads=True, progress=False, auto_adjust=True, timeout=30,
    )


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


def _frame_last_date(df: pd.DataFrame) -> dt.date | None:
    if df is None or len(df) == 0:
        return None
    try:
        idx = df.index[-1]
        if isinstance(idx, pd.Timestamp) and idx.tzinfo is not None:
            idx = idx.tz_convert(marketcal.WIB)
        elif isinstance(idx, dt.datetime) and idx.tzinfo is not None:
            idx = idx.astimezone(marketcal.WIB)
        return idx.date() if hasattr(idx, "date") else dt.date.fromisoformat(str(idx)[:10])
    except Exception:
        return None


def _recent_zero_vol_bars(df: pd.DataFrame, window: int = 20) -> int:
    if df is None or "volume" not in df or len(df) == 0:
        return 0
    tail = pd.to_numeric(df["volume"].tail(window), errors="coerce").fillna(0)
    return int((tail <= 0).sum())


def _round_buy_levels(close: float, atr: float | None) -> tuple[float, float | None, float | None]:
    """Bulatkan entry/SL/TP ke fraksi tick BEI yang sah."""
    entry = pricing.round_tick(close)
    if entry <= 0:
        entry = pricing.tick_size(close)
    if not atr or atr <= 0:
        return entry, None, None
    sl = pricing.round_tick(entry - 1.0 * atr)
    if sl >= entry:
        sl = entry - pricing.tick_size(max(1.0, entry - 1.0))
    sl = max(sl, 1.0)
    tp = pricing.round_tick(entry + 2.0 * atr)
    if tp <= entry:
        tp = entry + 2.0 * pricing.tick_size(entry)
    return entry, sl, tp


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


def _evaluate_detailed(
    frames: dict[str, pd.DataFrame],
    names: dict[str, str] | None = None,
    *,
    reference_date: dt.date | None = None,
    max_stale_days: int = MAX_STALE_DAYS,
) -> tuple[list[dict], list[str], dict]:
    """Hitung indikator + peringkat relatif sambil menyaring kualitas/suspen."""
    stats = {"skipped_stale": 0, "skipped_quality": 0, "quality_flagged": 0}
    if not frames:
        return [], [], stats

    ref_date = reference_date
    if ref_date is None:
        dates = [d for df in frames.values() if (d := _frame_last_date(df)) is not None]
        ref_date = max(dates) if dates else marketcal.now_wib().date()

    items: list[dict] = []
    skipped: list[str] = []
    for sym, df in frames.items():
        try:
            qual = data_quality.check(df, today=ref_date, min_bars=MIN_BARS)
            flags = qual.get("flags") or {}
            last_date = _frame_last_date(df)
            stale_days = (
                marketcal.trading_days_between(last_date, ref_date)
                if (last_date is not None and ref_date is not None)
                else 0
            )
            if stale_days > max_stale_days or flags.get("stale_days", 0) > max_stale_days:
                log.info("%s: dilewati karena data basi/suspen (%d hari bursa)", sym, stale_days)
                stats["skipped_stale"] += 1
                skipped.append(sym)
                continue
            if flags.get("no_data") or flags.get("short_history") or flags.get("bad_ohlc"):
                stats["skipped_quality"] += 1
                skipped.append(sym)
                continue
            if len(df) >= 20 and _recent_zero_vol_bars(df, 20) >= 20:
                log.info("%s: dilewati karena 20 bar terakhir volume 0 (tidur/suspen)", sym)
                stats["skipped_quality"] += 1
                skipped.append(sym)
                continue

            ind = analysis.compute_indicators(df)
            if len(ind) < MIN_BARS:
                stats["skipped_quality"] += 1
                skipped.append(sym)
                continue
            items.append({
                "ticker": sym, "ind": ind, "df": df,
                "quality": qual, "stale_days": stale_days,
                "recent_zero_vol": _recent_zero_vol_bars(df, 20),
            })
        except Exception:
            log.exception("indikator gagal untuk %s", sym)
            stats["skipped_quality"] += 1
            skipped.append(sym)

    if not items:
        return [], skipped, stats

    relatif.score_universe(items)

    rows: list[dict] = []
    for it in items:
        sym, ind = it["ticker"], it["ind"]
        qual = it["quality"]
        q_flags = dict(qual.get("flags") or {})
        recent_zero_vol = int(it.get("recent_zero_vol") or 0)
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
        vol_avg20 = _f(last.get("vol_avg20"))
        value_avg20 = None
        try:
            vol_avg20 = vol_avg20 or _f(
                ind["volume"].rolling(20).mean().iloc[-1])
            value_avg20 = _f(
                (ind["close"] * ind["volume"]).rolling(20).mean().iloc[-1])
        except Exception:
            value_avg20 = None
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

        dormant = recent_zero_vol >= MAX_ZERO_VOL_RECENT or q_flags.get("volume_nol", 0) >= 10
        if signal == "BELI" and dormant:
            signal = "TAHAN"
            reason = (
                f"Saham tidur ({recent_zero_vol}/20 bar terakhir volume 0) "
                "— ditahan dari kandidat BELI"
            )
        elif q_flags:
            reason += f" [kualitas: {', '.join(sorted(q_flags))}]"

        if q_flags or dormant:
            stats["quality_flagged"] += 1

        entry = sl = tp = None
        if signal == "BELI" and close:
            entry, sl, tp = _round_buy_levels(close, atr)

        try:
            trade_date = pd.Timestamp(ind.index[-1]).date().isoformat()
        except Exception:
            trade_date = marketcal.now_wib().date().isoformat()

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
            "vol_avg20": round(vol_avg20, 0) if vol_avg20 is not None else None,
            "value_avg20": round(value_avg20, 0) if value_avg20 is not None else None,
            "tech_score": round(tech_pct, 1) if tech_pct is not None else None,
            "rel_pct": round(rel_pct, 1) if rel_pct is not None else None,
            "signal": signal,
            "reason": reason,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "quality_ok": bool(qual.get("ok")),
            "quality_flags": q_flags,
            "stale_days": int(it.get("stale_days") or 0),
        })
    return rows, skipped, stats


def evaluate(
    frames: dict[str, pd.DataFrame],
    names: dict[str, str] | None = None,
    *,
    reference_date: dt.date | None = None,
    max_stale_days: int = MAX_STALE_DAYS,
) -> tuple[list[dict], list[str]]:
    """Hitung indikator + peringkat relatif untuk semua emiten yang lolos kualitas."""
    rows, skipped, _ = _evaluate_detailed(
        frames, names=names, reference_date=reference_date, max_stale_days=max_stale_days,
    )
    return rows, skipped


# ------------------------------------------------------------- histori ---
def _close_pending_history(frames: dict[str, pd.DataFrame], trade_date: str):
    """Tutup histori sinyal yang horizon-nya sudah terlewati.

    Memakai frame yang sudah diunduh untuk scan ini — nol permintaan jaringan
    tambahan. Baris yang belum cukup umur dicoba lagi pada scan berikutnya.
    """
    try:
        pending = store.pending_scan_history(trade_date)
        if not pending:
            return
        resolved = scorecard.resolve_rows(pending, frames)
        if resolved:
            n = store.close_scan_history(resolved)
            log.info("histori sinyal: %d baris ditutup", n)
    except Exception:
        log.exception("gagal menutup histori sinyal")


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
    ref_date = None if downloader is not None else marketcal.now_wib().date()
    rows, skipped, q_stats = _evaluate_detailed(frames, reference_date=ref_date)
    if not rows:
        raise RuntimeError("Tidak ada emiten dengan data harga yang cukup")

    trade_dates = [r["trade_date"] for r in rows if r.get("trade_date")]
    trade_date = max(trade_dates) if trade_dates else _today_wib()
    trade_date_min = min(trade_dates) if trade_dates else trade_date
    _close_pending_history(frames, trade_date)
    counts = {}
    for r in rows:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1

    umeta = universe.meta()
    meta = {
        "scanned_at": marketcal.now_wib().strftime("%Y-%m-%dT%H:%M:%S"),
        "trade_date": trade_date,
        "trade_date_min": trade_date_min,
        "universe": len(syms),
        "ok": len(rows),
        "skipped": len(skipped),
        "skipped_stale": q_stats["skipped_stale"],
        "skipped_quality": q_stats["skipped_quality"],
        "quality_flagged": q_stats["quality_flagged"],
        "max_stale_days": MAX_STALE_DAYS,
        "failed": max(0, len(syms) - len(rows)),
        "elapsed_sec": round(time.time() - t0, 1),
        "universe_source": umeta.get("source", ""),
        "universe_snapshot": umeta.get("fetched_at", ""),
        "buy": counts.get("BELI", 0),
        "sell": counts.get("JUAL", 0),
        "hold": counts.get("TAHAN", 0),
    }
    store.save_market_scan(rows, meta)
    # Snapshot ditimpa tiap scan; histori inilah yang bisa diukur belakangan.
    try:
        store.append_scan_history(rows, trade_date)
    except Exception:
        log.exception("gagal menyimpan histori sinyal")
    return {"cached": False, "counts": counts, "meta": meta,
            "skipped": skipped}

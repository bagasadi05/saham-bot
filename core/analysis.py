"""Perhitungan indikator teknikal & skor rating saham."""
from __future__ import annotations

import math

import pandas as pd

TREND_LABELS = {
    "STRONG BUY": "Momentum Kuat",
    "BUY": "Condong Menguat",
    "NETRAL": "Netral",
    "HOLD": "Netral",
    "SELL": "Melemah",
    "STRONG SELL": "Tekanan Turun",
}
SCORE_DISCLAIMER = "Indikator tren historis, bukan prediksi pasti — IC harian ±0,01"


def describe_rating(rating: str | None) -> str:
    """Ubah kode rating internal menjadi label tren deskriptif non-prediktif."""
    key = str(rating or "").strip().upper()
    return TREND_LABELS.get(key, "Netral")


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """RSI Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    # harga datar sempurna -> avg_gain=avg_loss=0 -> netral 50, bukan NaN
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    return out.where(~both_zero, 50.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def support_resistance(df: pd.DataFrame, lookback: int = 20) -> dict:
    """S/R sederhana: support = low terendah & resistance = high tertinggi 20 hari."""
    recent = df.tail(lookback)
    low = round(float(recent["low"].min()), 2)
    high = round(float(recent["high"].max()), 2)
    return {"support": low, "resistance": high,
            "high_recent": high, "low_recent": low}


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    fallbacks: list[str] = []
    df["sma20"] = sma(df["close"], 20)
    df["sma50"] = sma(df["close"], 50)
    df["sma200"] = sma(df["close"], 200)
    df["vol_avg20"] = sma(df["volume"], 20)
    df["rsi14"] = rsi(df["close"])
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(df["close"])
    df["high20"] = df["high"].rolling(20).max()
    df["low20"] = df["low"].rolling(20).min()
    df["ret1d"] = df["close"].pct_change() * 100
    df["ret20d"] = df["close"].pct_change(20) * 100
    # volatilitas 20 hari (standar deviasi return harian, fraksi) — dipakai
    # faktor relatif; definisinya sama persis dengan harness riset
    df["volat20"] = df["ret1d"].rolling(20).std() / 100

    # ---- indikator pro (pandas-ta-classic), untuk sinyal konfirmasi & SL/TP ----
    try:
        import pandas_ta_classic  # noqa: F401 — import ini memasang ekstensi df.ta
        has_ta = True
    except Exception:
        has_ta = False
        fallbacks.append(
            "pandas-ta-classic tidak tersedia: BB/OBV/StochRSI dilewati")

    atr = None
    if has_ta:
        try:
            atr = df.ta.atr(14)
        except Exception:
            atr = None
    atr_ok = (atr is not None
              and not getattr(atr, "isna", lambda: pd.Series([True]))().all())
    if not atr_ok:
        fallbacks.append("ATR 14 memakai rentang high-low (fallback)")
    df["atr14"] = atr if atr_ok else (df["high"] - df["low"]).rolling(14).mean()

    if has_ta:
        try:
            bb = df.ta.bbands(20, std=2.0)
            if bb is not None and bb.shape[1] >= 3:
                df["bb_upper"] = bb.iloc[:, 2]  # BBL/BBM/BBU
                df["bb_mid"] = bb.iloc[:, 1]
                df["bb_lower"] = bb.iloc[:, 0]
        except Exception:
            fallbacks.append("Bollinger gagal dihitung")
        try:
            obv = df.ta.obv()
            if obv is not None:
                df["obv"] = obv
                df["obv_slope20"] = obv.diff(20)
        except Exception:
            fallbacks.append("OBV gagal dihitung")
        try:
            sr = df.ta.stochrsi()
            if sr is not None and sr.shape[1] >= 1:
                st = sr.iloc[:, 0]
                # Harga datar membuat RSI konstan (0/0 -> 0 di pandas-ta), bukan oversold
                rsi_span = df["rsi14"].rolling(14).max() - df["rsi14"].rolling(14).min()
                df["stochrsi"] = st.where(rsi_span > 1e-9, float("nan"))
        except Exception:
            fallbacks.append("StochRSI gagal dihitung")

    df.attrs["fallbacks"] = fallbacks
    df.attrs["has_ta"] = has_ta
    return df


def extra_confirmations(ind: pd.DataFrame) -> list[str]:
    """Sinyal konfirmasi dari indikator pro — TIDAK mengubah skor yang sudah
    dibacktest, hanya menambah konteks (dipakai di laporan /analisis)."""
    last = ind.iloc[-1]
    out: list[str] = []
    if pd.isna(last.get("atr14", float("nan"))):
        return out
    atr_val = float(last["atr14"])
    close = float(last["close"])
    if close > 0 and atr_val > 0:
        atr_pct = atr_val / close * 100
        out.append(f"ATR 14: {atr_pct:.1f}% dari harga (dasar SL ±1×ATR ≈ "
                   f"{money_fmt(max(0.0, close - atr_val))})")
    else:
        out.append("ATR 14: 0.0% dari harga (harga datar / tidak bergerak)")
    bb_mid, bb_upper = last.get("bb_mid"), last.get("bb_upper")
    bb_lower = last.get("bb_lower")
    if not any(pd.isna(v) for v in (bb_mid, bb_upper, bb_lower)) and float(bb_mid) > 0:
        width = float(bb_upper) - float(bb_lower)
        if width <= 0:
            out.append("Bollinger: rentang 0% (harga datar / tidak bergerak)")
        else:
            squeeze = width / float(bb_mid) * 100
            pos = (close - float(bb_lower)) / width * 100
            if squeeze < 8:
                out.append(f"Bollinger SQUEEZE ({squeeze:.1f}%) — rentang makin "
                           "sempit: sering diikuti pergerakan besar 💤")
            elif pos > 100:
                out.append("🚨 Harga di luar Bollinger atas — bawaan ekstrem, waspada koreksi")
            elif pos < 0:
                out.append("🚨 Harga di bawah Bollinger bawah — ekstrem lainnya, waspada bertahan lama")
            else:
                out.append(f"Bollinger: harga di {pos:.0f}% rentang band")
    if not pd.isna(last.get("stochrsi", float("nan"))):
        st = float(last["stochrsi"])
        if st < 20:
            out.append(f"StochRSI {st:.0f} — oversold pendek ⏱")
        elif st > 80:
            out.append(f"StochRSI {st:.0f} — overbought pendek 🥵")
    obv_slope = last.get("obv_slope20")
    if obv_slope is not None and not pd.isna(obv_slope):
        if obv_slope > 0:
            direction = "akumulasi 💰"
        elif obv_slope < 0:
            direction = "distribusi 🚪"
        else:
            direction = "datar ➖"
        out.append(f"OBV 20 hari: {direction} ({obv_slope:+,.0f})")
    return out


def money_fmt(v: float) -> str:
    """Rupiah tanpa desimal — satu implementasi di core/textutil.py."""
    from core.textutil import rupiah

    return rupiah(v)


SCORE_WEIGHTS = {
    "price_above_sma50": 15,      # uptrend jangka menengah
    "price_above_sma200": 15,     # uptrend jangka panjang
    "sma20_above_sma50": 10,      # tren naik pendek-menetengah
    "sma50_above_sma200": 10,     # golden alignment
    "macd_bullish": 10,           # MACD di atas garis sinyal
    "rsi_goldilocks": 10,         # RSI 50-70: momentum sehat
    "breakout_20d": 10,           # breakout high 20 hari
    "volume_momentum": 5,         # volume > rata-rata saat naik
    "price_above_sma20": 5,
}
MAX_SCORE = sum(SCORE_WEIGHTS.values())  # 90


def _cmp(a, b) -> bool | None:
    """Perbandingan yang jujur soal data kurang: NaN → None (netral).

    Dulu `close > NaN` bernilai False, sehingga emiten berriwayat <200 hari
    otomatis dianggap "di bawah MA200" dan skornya dihukum tanpa alasan.
    """
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return bool(a > b)


def score_from_rows(last, prev) -> dict:
    """Skor dari dua baris indikator (baris hari ini & kemarin).
    Dipakai oleh score_stock() dan modul backtest."""
    close = _f(last, "close")
    rsi_v = _f(last, "rsi14")
    ret1d_v = _f(last, "ret1d")
    signals_bull: list[str] = []
    signals_bear: list[str] = []
    score = 0

    def add(weight: float, bull: bool | None, bull_msg: str, bear_msg: str):
        nonlocal score
        if bull is None:
            return  # indikator belum terbentuk → netral, jangan dihukum
        if bull:
            signals_bull.append(bull_msg)
            score += weight
        else:
            signals_bear.append(bear_msg)
            score -= weight

    add(SCORE_WEIGHTS["price_above_sma50"], _cmp(close, _f(last, "sma50")),
        "Harga di atas MA50", "Harga di bawah MA50")
    add(SCORE_WEIGHTS["price_above_sma200"], _cmp(close, _f(last, "sma200")),
        "Harga di atas MA200", "Harga di bawah MA200")
    add(SCORE_WEIGHTS["sma20_above_sma50"], _cmp(_f(last, "sma20"), _f(last, "sma50")),
        "MA20 > MA50 (tren naik pendek)", "MA20 < MA50 (tren turun)")
    add(SCORE_WEIGHTS["sma50_above_sma200"], _cmp(_f(last, "sma50"), _f(last, "sma200")),
        "MA50 > MA200 (golden layout)", "MA50 < MA200 (death layout)")
    add(SCORE_WEIGHTS["macd_bullish"], _cmp(_f(last, "macd"), _f(last, "macd_signal")),
        "MACD bullish", "MACD bearish")
    # RSI: hanya <30 / >70 yang benar-benar ekstrem; 30-50 itu sekadar lemah.
    # (NaN = indikator belum terbentuk -> netral, jangan dihukum.)
    if pd.isna(rsi_v):
        rsi_bear = "RSI tidak tersedia"
    elif rsi_v >= 70:
        rsi_bear = f"RSI overbought ({rsi_v:.0f}) - jenuh beli"
    elif rsi_v <= 30:
        rsi_bear = f"RSI oversold ({rsi_v:.0f}) - jenuh jual"
    else:
        rsi_bear = f"RSI lemah ({rsi_v:.0f}) - momentum di bawah 50"
    add(SCORE_WEIGHTS["rsi_goldilocks"],
        None if pd.isna(rsi_v) else 50 <= rsi_v <= 70,
        f"RSI sehat ({rsi_v:.0f})" if not pd.isna(rsi_v) else "RSI tidak tersedia",
        rsi_bear)
    # catatan: high20 memuat high hari ini, jadi ini "dekat/atas puncak",
    # bukan konfirmasi breakout murni (itu tugas alert breakout).
    high20 = _f(last, "high20")
    add(SCORE_WEIGHTS["breakout_20d"],
        None if pd.isna(high20) else close >= high20 * 0.998,
        "Harga di dekat/atas puncak 20 hari",
        "Belum di dekat puncak 20 hari")
    # Volume: jangan klaim "tekanan jual" dari volume rendah — sebutkan faktanya.
    # Volume hari berjalan juga belum lengkap kalau pasar masih buka (candle
    # hari ini vs rata-rata hari penuh itu apel lawan jeruk).
    vol = _f(last, "volume")
    vol_avg = _f(last, "vol_avg20")
    vol_missing = pd.isna(vol) or pd.isna(vol_avg) or pd.isna(ret1d_v) or vol_avg <= 0
    vol_bull = (not vol_missing) and (vol > vol_avg and ret1d_v > 0)
    if not vol_missing and vol > vol_avg:
        vol_bear = "Volume di atas rata-rata, tapi harga turun"
    elif not vol_missing and ret1d_v > 0:
        vol_bear = "Harga naik, tapi volume di bawah rata-rata"
    else:
        vol_bear = "Volume di bawah rata-rata, harga turun"
    try:
        last_date = pd.Timestamp(last.name).date()
        now_wib = pd.Timestamp.now(tz="Asia/Jakarta")
        if last_date == now_wib.date() and now_wib.hour < 15:
            vol_bear += " (pasar masih buka — volume hari ini belum lengkap)"
    except Exception:
        pass
    add(SCORE_WEIGHTS["volume_momentum"],
        None if vol_missing else vol_bull,
        "Volume di atas rata-rata dengan harga naik", vol_bear)
    add(SCORE_WEIGHTS["price_above_sma20"], _cmp(close, _f(last, "sma20")),
        "Harga di atas MA20", "Harga di bawah MA20")

    macd_note = None
    if prev is not None and not pd.isna(prev["macd"]) and not pd.isna(prev["macd_signal"]):
        if _f(prev, "macd") <= _f(prev, "macd_signal") and _f(last, "macd") > _f(last, "macd_signal"):
            macd_note = "MACD baru saja golden cross!"
        elif _f(prev, "macd") >= _f(prev, "macd_signal") and _f(last, "macd") < _f(last, "macd_signal"):
            macd_note = "MACD baru saja death cross!"

    pct = score / MAX_SCORE  # -1..1
    if pct >= 0.5:
        rating, emoji = "STRONG BUY", "🚀"
    elif pct >= 0.2:
        rating, emoji = "BUY", "📈"
    elif pct > -0.2:
        rating, emoji = "NETRAL", "😐"
    elif pct > -0.5:
        rating, emoji = "SELL", "📉"
    else:
        rating, emoji = "STRONG SELL", "🔻"

    return {
        "score": score,
        "max": MAX_SCORE,
        "pct": pct * 100,
        "rating": rating,
        "trend_label": TREND_LABELS[rating],
        "disclaimer": SCORE_DISCLAIMER,
        "emoji": emoji,
        "bull": signals_bull,
        "bear": signals_bear,
        "macd_note": macd_note,
    }


def _f(row, col: str) -> float:
    return float(row[col])


def score_stock(ind: pd.DataFrame) -> dict:
    """Skor -MAX_SCORE..+MAX_SCORE beserta daftar sinyal positif/negatif."""
    return score_from_rows(ind.iloc[-1], ind.iloc[-2])


def _as_of(ind: pd.DataFrame) -> str:
    """Tanggal bar terakhir (ISO) untuk laporan 'data per'."""
    try:
        idx = ind.index[-1]
        return (idx.date() if hasattr(idx, "date") else idx).isoformat()
    except Exception:
        return ""


def _num_or(val, ndigits: int | None = None, default=None):
    if val is None or pd.isna(val):
        return default
    f = float(val)
    if not math.isfinite(f):
        return default
    return round(f, ndigits) if ndigits is not None else f


def last_snapshot(ind: pd.DataFrame) -> dict:
    last = ind.iloc[-1]
    close = round(float(last["close"]), 2)
    vol_avg = _num_or(last.get("vol_avg20"), default=1.0)
    if vol_avg is None or vol_avg <= 0:
        vol_avg = 1.0
    return {
        "close": close,
        "as_of": _as_of(ind),
        "ret1d": _num_or(last.get("ret1d"), 2, 0.0),
        "ret20d": _num_or(last.get("ret20d"), 2, 0.0),
        "rsi": _num_or(last.get("rsi14"), 1, None),
        "macd_hist": _num_or(last.get("macd_hist"), 3, 0.0),
        "volume": _num_or(last.get("volume"), default=0.0),
        "vol_avg20": vol_avg,
        "sma20": _num_or(last.get("sma20"), 2, None),
        "sma50": _num_or(last.get("sma50"), 2, None),
        "sma200": _num_or(last.get("sma200"), 2, None),
        "high20": _num_or(last.get("high20"), 2, close),
        "low20": _num_or(last.get("low20"), 2, close),
    }


def weekly_trend(df_weekly: pd.DataFrame | None,
                 daily_score_pct: float | None = None) -> dict | None:
    """[P9] Konfirmasi tren mingguan: close vs MA20 mingguan + cek divergensi."""
    if df_weekly is None or len(df_weekly) < 20 or "close" not in df_weekly.columns:
        return None
    close = df_weekly["close"].dropna().astype(float)
    if len(close) < 20:
        return None
    ma20 = close.rolling(20).mean().iloc[-1]
    last_c = float(close.iloc[-1])
    if pd.isna(ma20) or ma20 <= 0 or not math.isfinite(last_c):
        return None
    ma20 = float(ma20)
    pct_vs_ma = (last_c / ma20 - 1.0) * 100.0
    direction = "naik" if last_c > ma20 else ("turun" if last_c < ma20 else "datar")
    warning = None
    if daily_score_pct is not None:
        if direction == "turun" and daily_score_pct >= 20:
            warning = "daily membaik tapi weekly masih downtrend — pantulan, bukan pembalikan"
        elif direction == "naik" and daily_score_pct <= -20:
            warning = "daily melemah di dalam uptrend mingguan — koreksi jangka pendek"
    return {
        "direction": direction,
        "close": round(last_c, 2),
        "ma20": round(ma20, 2),
        "pct_vs_ma": round(pct_vs_ma, 1),
        "warning": warning,
    }


def analyze(ticker: str, df_raw) -> dict:
    if df_raw is None or len(df_raw) == 0:
        raise ValueError(
            f"data '{ticker.upper()}' tidak dapat diambil (sumber gagal/"
            "rate-limit); silakan coba lagi nanti")
    if len(df_raw) < 30:
        raise ValueError(
            f"riwayat harga '{ticker.upper()}' baru {len(df_raw)} hari — "
            "minimal 30 hari bursa agar MA/RSI bisa dihitung")
    ind = compute_indicators(df_raw)
    # [jujur] riwayat pendek membuat MA200 NaN dan skor otomatis bias turun
    history_note = ""
    if len(ind) < 200:
        history_note = (
            f"⚠️ <i>Riwayat harga cuma {len(ind)} hari (<200): MA200 belum "
            "terbentuk — sinyal jangka panjang dianggap netral, bukan negatif.</i>\n")
    attrs = getattr(ind, "attrs", {}) or {}
    out = {
        "ticker": ticker.upper(),
        "ind": ind,
        "snap": last_snapshot(ind),
        "score": score_stock(ind),
        "sr": support_resistance(ind),
        "history_note": history_note,
        "fallbacks": list(attrs.get("fallbacks") or []),
    }
    if attrs.get("provenance") is not None:
        out["provenance"] = attrs.get("provenance")
    elif any(k in attrs for k in ("provider", "last_bar_date", "repaired_count", "stale")):
        out["provenance"] = {
            k: attrs[k]
            for k in ("provider", "last_bar_date", "repaired_count", "stale")
            if k in attrs
        }
    return out

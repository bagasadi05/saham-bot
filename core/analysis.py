"""Perhitungan indikator teknikal & skor rating saham."""
from __future__ import annotations

import pandas as pd


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
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def support_resistance(df: pd.DataFrame, lookback: int = 20) -> dict:
    """S/R sederhana: support = min low & pivot bawah, resistance = max high 20 hari terakhir."""
    recent = df.tail(lookback)
    close = float(df["close"].iloc[-1])
    return {
        "support": round(float(recent["low"].min()), 2),
        "resistance": round(float(recent["low"].max() if recent["low"].max() < close else recent["high"].max()), 2),
        "high_recent": round(float(recent["high"].max()), 2),
        "low_recent": round(float(recent["low"].min()), 2),
    }


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
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

    # ---- indikator pro (pandas-ta-classic), untuk sinyal konfirmasi & SL/TP ----
    try:
        import pandas_ta_classic  # noqa: F401 — import ini memasang ekstensi df.ta
        atr = df.ta.atr(14)
        bb = df.ta.bbands(20, std=2.0)
        df["atr14"] = atr
        df["bb_upper"] = bb.iloc[:, 2]  # BBL/BBM/BBU
        df["bb_mid"] = bb.iloc[:, 1]
        df["bb_lower"] = bb.iloc[:, 0]
        df["obv"] = df.ta.obv()
        df["stochrsi"] = df.ta.stochrsi().iloc[:, 0]  # %K primera
        df["obv_slope20"] = df["obv"].diff(20)
    except Exception:  # library tidak terpasang → indikator pro dilewati
        df["atr14"] = ((df["high"] - df["low"]).rolling(14).mean())

    return df


def extra_confirmations(ind: pd.DataFrame) -> list[str]:
    """Sinyal konfirmasi dari indikator pro — TIDAK mengubah skor yang sudah
    dibacktest, hanya menambah konteks (dipakai di laporan /analisis)."""
    last = ind.iloc[-1]
    out: list[str] = []
    if pd.isna(last.get("atr14", float("nan"))):
        return out
    atr_pct = float(last["atr14"]) / float(last["close"]) * 100
    out.append(f"ATR 14: {atr_pct:.1f}% dari harga (dasar SL ±1×ATR ≈ "
               f"{money_fmt(float(last['close'] - last['atr14']))})")
    bb_mid, bb_upper = last.get("bb_mid"), last.get("bb_upper")
    bb_lower, close = last.get("bb_lower"), float(last["close"])
    if not any(pd.isna(v) for v in (bb_mid, bb_upper, bb_lower)):
        width = float(bb_upper) - float(bb_lower)
        squeeze = width / float(bb_mid) * 100
        pos = (close - float(bb_lower)) / width * 100 if width else 50
        if squeeze < 8:
            out.append(f"Bollinger SQUEEZE ({squeeze:.1f}%) — rentang makin "
                       "sempit: sering dikuti pergerakan besar 💤")
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
        direction = "akumulasi 💰" if obv_slope > 0 else "distribusi 🚪"
        out.append(f"OBV 20 hari: {direction} ({obv_slope:+,.0f})")
    return out


def money_fmt(v: float) -> str:
    return f"Rp{v:,.0f}".replace(",", ".")


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


def score_from_rows(last, prev) -> dict:
    """Skor dari dua baris indikator (baris hari ini & kemarin).
    Dipakai oleh score_stock() dan modul backtest."""
    close = _f(last, "close")
    rsi_v = _f(last, "rsi14")
    ret1d_v = _f(last, "ret1d")
    signals_bull: list[str] = []
    signals_bear: list[str] = []
    score = 0

    def add(weight: float, bull: bool, bull_msg: str, bear_msg: str):
        nonlocal score
        if bull:
            signals_bull.append(bull_msg)
            score += weight
        else:
            signals_bear.append(bear_msg)
            score -= weight

    add(SCORE_WEIGHTS["price_above_sma50"], close > _f(last, "sma50"),
        "Harga di atas MA50", "Harga di bawah MA50")
    add(SCORE_WEIGHTS["price_above_sma200"], close > _f(last, "sma200"),
        "Harga di atas MA200", "Harga di bawah MA200")
    add(SCORE_WEIGHTS["sma20_above_sma50"], _f(last, "sma20") > _f(last, "sma50"),
        "MA20 > MA50 (tren naik pendek)", "MA20 < MA50 (tren turun)")
    add(SCORE_WEIGHTS["sma50_above_sma200"], _f(last, "sma50") > _f(last, "sma200"),
        "MA50 > MA200 (golden layout)", "MA50 < MA200 (death layout)")
    add(SCORE_WEIGHTS["macd_bullish"], _f(last, "macd") > _f(last, "macd_signal"),
        "MACD bullish", "MACD bearish")
    add(SCORE_WEIGHTS["rsi_goldilocks"], 50 <= rsi_v <= 70,
        f"RSI sehat ({rsi_v:.0f})",
        f"RSI ekstrem ({rsi_v:.0f}) - jenuh beli/jual")
    add(SCORE_WEIGHTS["breakout_20d"], close >= _f(last, "high20") * 0.998,
        "Baru breakout high 20 hari", "Belum breakout / turun dari high 20 hari")
    add(SCORE_WEIGHTS["volume_momentum"],
        _f(last, "volume") > _f(last, "vol_avg20") and ret1d_v > 0,
        "Volume di atas rata-rata dengan harga naik",
        "Volume biasa / tekanan jual")
    add(SCORE_WEIGHTS["price_above_sma20"], close > _f(last, "sma20"),
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


def last_snapshot(ind: pd.DataFrame) -> dict:
    last = ind.iloc[-1]
    return {
        "close": round(float(last["close"]), 2),
        "ret1d": round(float(last["ret1d"]), 2),
        "ret20d": round(float(last["ret20d"]), 2),
        "rsi": round(float(last["rsi14"]), 1),
        "macd_hist": round(float(last["macd_hist"]), 3),
        "volume": float(last["volume"]),
        "vol_avg20": float(last["vol_avg20"]) or 1.0,
        "sma20": round(float(last["sma20"]), 2) if not pd.isna(last["sma20"]) else None,
        "sma50": round(float(last["sma50"]), 2) if not pd.isna(last["sma50"]) else None,
        "sma200": round(float(last["sma200"]), 2) if not pd.isna(last["sma200"]) else None,
        "high20": round(float(last["high20"]), 2),
        "low20": round(float(last["low20"]), 2),
    }


def analyze(ticker: str, df_raw) -> dict:
    ind = compute_indicators(df_raw)
    return {
        "ticker": ticker.upper(),
        "ind": ind,
        "snap": last_snapshot(ind),
        "score": score_stock(ind),
        "sr": support_resistance(ind),
    }

"""Logika alert otomatis: mendeteksi kondisi pemicu dari data terbaru."""
from __future__ import annotations

import pandas as pd

KIND_LABEL = {
    "jump": "🏃 Lonjakan harga",
    "drop": "💥 Penurunan tajam",
    "breakout": "🚀 Breakout 20 hari",
    "breakdown": "⚠️ Breakdown support 20 hari",
    "oversold": "🟣 RSI oversold (<30)",
    "overbought": "🔥 RSI overbought (>70)",
    "ma_golden": "✨ MACD golden cross",
    "ma_death": "💀 MACD death cross",
    "vol_spike": "📢 Lonjakan volume",
}

KIND_EXAMPLES = {
    "jump": "harga naik >=5% dalam sehari",
    "drop": "harga turun <=-5% dalam sehari",
    "breakout": "close menembus high 20 hari",
    "breakdown": "close jatuh di bawah low 20 hari",
    "oversold": "RSI 14 di bawah 30",
    "overbought": "RSI 14 di atas 70",
    "ma_golden": "MACD memotong naik signal line",
    "ma_death": "MACD memotong turun signal line",
    "vol_spike": "volume >=3x rata-rata 20 hari",
}

ALL_KINDS = set(KIND_LABEL)


def check_triggers(ind: pd.DataFrame) -> list[str]:
    """Cek baris terakhir & membandingkan dengan baris sebelumnya. Return daftar pemicu."""
    if len(ind) < 25:
        return []
    last, prev = ind.iloc[-1], ind.iloc[-2]
    ret = float(last.get("ret1d", 0)) or 0
    triggers: list[str] = []

    # Indikator "level" — cek posisi sekarang
    if ret >= 5:
        triggers.append("jump")
    if ret <= -5:
        triggers.append("drop")
    if pd.notna(last["high20"]) and float(last["close"]) > float(last["high20"]) * 1.001:
        triggers.append("breakout")
    if pd.notna(last["low20"]) and float(last["close"]) < float(last["low20"]) * 0.999:
        triggers.append("breakdown")
    if float(last["rsi14"]) < 30:
        triggers.append("oversold")
    if float(last["rsi14"]) > 70:
        triggers.append("overbought")
    if float(last["vol_avg20"]) and float(last["volume"]) >= 3 * float(last["vol_avg20"]):
        triggers.append("vol_spike")

    # Indikator "cross" — butuh nilai prev
    if pd.notna(prev["macd"]) and pd.notna(prev["macd_signal"]):
        cross_golden = float(prev["macd"]) <= float(prev["macd_signal"]) < float(last["macd"])
        cross_death = float(prev["macd"]) >= float(prev["macd_signal"]) > float(last["macd"])
        if cross_golden:
            triggers.append("ma_golden")
        if cross_death:
            triggers.append("ma_death")

    return triggers


def build_alert_message(symbol: str, triggers: list[str], snap: dict, score: dict) -> str:
    lines = [f"🔔 <b>ALERT</b> — <b>{symbol}</b>",
             f"Harga: Rp{snap['close']:,} ({snap['ret1d']:+.2f}% HARI INI)",
             f"Skor: {score['pct']:+.0f}% {score['emoji']} {score['rating']}",
             "",
             "Pemicu aktif:"]
    for t in triggers:
        lines.append(f"  {KIND_LABEL.get(t, t)}")
    return "\n".join(lines)


def market_hours(jkt_hour: int, jkt_minute: int, weekday: int, open_=None, close=None) -> bool:
    """Cek apakah sedang dalam jam bursa (Senin-Jumat 09:00-16:00 WIB)."""
    if weekday >= 5:  # Sabtu = 5, Minggu = 6
        return False
    minutes_into_day = jkt_hour * 60 + jkt_minute
    o = (open_ or (9, 0))
    c = (close or (16, 0))
    return o[0] * 60 + o[1] <= minutes_into_day <= c[0] * 60 + c[1]

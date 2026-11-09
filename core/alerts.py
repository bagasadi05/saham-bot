"""Logika alert otomatis: mendeteksi kondisi pemicu dari data terbaru."""
from __future__ import annotations

import html

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


def match_kinds(triggered: list[str], kinds) -> set[str]:
    """Irisan pemicu aktif dengan jenis yang didaftarkan user.

    Dipisah dari bot supaya bug tipe (list & set) tidak terulang dan bisa diuji.
    """
    return set(triggered) & set(kinds)


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
    # high20/low20 baris terakhir SUDAH memuat high/low hari ini, sehingga close
    # tidak mungkin melewatinya. Pakai level 20 hari sampai dengan kemarin.
    high20_prev = float(prev["high20"]) if pd.notna(prev.get("high20")) else None
    low20_prev = float(prev["low20"]) if pd.notna(prev.get("low20")) else None
    close_now = float(last["close"])
    if high20_prev and close_now > high20_prev * 1.001:
        triggers.append("breakout")
    if low20_prev and close_now < low20_prev * 0.999:
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
    lines = [f"🔔 <b>ALERT</b> — <b>{html.escape(str(symbol))}</b>",
             f"Harga: Rp{snap['close']:,.0f}".replace(",", ".") + f" ({snap['ret1d']:+.2f}% HARI INI)",
             f"Skor: {score['pct']:+.0f}% {score['emoji']} {html.escape(str(score['rating']))}",
             "",
             "Pemicu aktif:"]
    for t in triggers:
        # label berisi '<' / '>' (mis. 'RSI oversold (<30)') — escape agar
        # pesan HTML Telegram tidak gagal parse
        lines.append(f"  {html.escape(KIND_LABEL.get(t, t))}")
    return "\n".join(lines)

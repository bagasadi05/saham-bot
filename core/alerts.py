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
    close_raw = last.get("close")
    if close_raw is None or pd.isna(close_raw):
        return []
    close_now = float(close_raw)

    ret_raw = last.get("ret1d")
    ret = float(ret_raw) if ret_raw is not None and pd.notna(ret_raw) else 0.0
    triggers: list[str] = []

    # Indikator "level" — cek posisi sekarang
    if ret >= 5:
        triggers.append("jump")
    if ret <= -5:
        triggers.append("drop")
    # high20/low20 baris terakhir SUDAH memuat high/low hari ini, sehingga close
    # tidak mungkin melewatinya. Pakai level 20 hari sampai dengan kemarin.
    high20_prev = (
        float(prev["high20"])
        if pd.notna(prev.get("high20")) and float(prev["high20"]) > 0
        else None
    )
    low20_prev = (
        float(prev["low20"])
        if pd.notna(prev.get("low20")) and float(prev["low20"]) > 0
        else None
    )
    if high20_prev and close_now > high20_prev * 1.001:
        triggers.append("breakout")
    if low20_prev and close_now < low20_prev * 0.999:
        triggers.append("breakdown")

    rsi_raw = last.get("rsi14")
    if rsi_raw is not None and pd.notna(rsi_raw):
        rsi_v = float(rsi_raw)
        if rsi_v < 30:
            triggers.append("oversold")
        if rsi_v > 70:
            triggers.append("overbought")

    vol_raw, vavg_raw = last.get("volume"), last.get("vol_avg20")
    if (
        vol_raw is not None and vavg_raw is not None
        and pd.notna(vol_raw) and pd.notna(vavg_raw) and float(vavg_raw) > 0
    ):
        if float(vol_raw) >= 3 * float(vavg_raw):
            triggers.append("vol_spike")

    # Indikator "cross" — butuh nilai prev & last yang valid
    p_macd, p_sig = prev.get("macd"), prev.get("macd_signal")
    l_macd, l_sig = last.get("macd"), last.get("macd_signal")
    if (
        pd.notna(p_macd) and pd.notna(p_sig)
        and pd.notna(l_macd) and pd.notna(l_sig)
    ):
        cross_golden = float(p_macd) <= float(p_sig) and float(l_macd) > float(l_sig)
        cross_death = float(p_macd) >= float(p_sig) and float(l_macd) < float(l_sig)
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

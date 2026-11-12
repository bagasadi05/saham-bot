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

# [P16] Zona beli untuk watchlist. Bukan sinyal pasti: hanya kondisi yang
# biasanya dipakai sebagai titik perhatian sebelum memutuskan.
ZONE_LABEL = {
    "support": "🧲 Memantul dari support 20 hari",
    "rsi": "🟣 RSI pulih dari oversold (<30 -> >=30)",
    "breakout": "🚀 Breakout 20 hari + volume >= 1,5x rata-rata",
}
ZONE_VOL_MULT = 1.5


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if pd.notna(x) else None


def buy_zones(ind: pd.DataFrame) -> list[str]:
    """Deteksi zona beli dari baris terakhir indikator.

    - support : low hari ini menyentuh low20 sebelumnya (<= +0,5%) DAN close
                naik dibanding kemarin - pantulan, bukan breakdown.
    - rsi     : RSI14 kemarin < 30 dan hari ini >= 30 (pulih dari oversold).
    - breakout: close > high20 sebelumnya x 1,001 DAN volume > 1,5x rata-rata
                20 hari.

    Level high20/low20 memakai baris SEBELUMNYA, sama seperti `check_triggers`
    - high20 baris terakhir sudah memuat harga hari ini.
    """
    if len(ind) < 25:
        return []
    last, prev = ind.iloc[-1], ind.iloc[-2]
    close = _f(last.get("close"))
    prev_close = _f(prev.get("close"))
    low_today = _f(last.get("low"))
    low20 = _f(prev.get("low20"))
    high20 = _f(prev.get("high20"))
    rsi_now = _f(last.get("rsi14"))
    rsi_prev = _f(prev.get("rsi14"))
    vol = _f(last.get("volume"))
    vavg = _f(last.get("vol_avg20"))

    zones: list[str] = []
    if (low20 and low_today and low_today <= low20 * 1.005
            and close is not None and prev_close is not None
            and close > prev_close):
        zones.append("support")
    if (rsi_prev is not None and rsi_now is not None
            and rsi_prev < 30 <= rsi_now):
        zones.append("rsi")
    if (high20 and close is not None and close > high20 * 1.001
            and vol is not None and vavg and vol > ZONE_VOL_MULT * vavg):
        zones.append("breakout")
    return zones


def build_zone_message(symbol: str, zones: list[str], snap: dict,
                       sl: float | None = None,
                       tp: float | None = None) -> str:
    """Pesan saat emiten MASUK zona beli (bukan tiap scan)."""
    sym = html.escape(str(symbol))
    close = float(snap["close"])
    ret = snap.get("ret1d")
    lines = [f"🎯 <b>MASUK ZONA BELI</b> — <b>{sym}</b>",
             f"Harga: <b>Rp{close:,.0f}</b>".replace(",", ".")
             + (f" ({ret:+.2f}% hari ini)" if ret is not None else ""),
             "", "Zona yang terpicu:"]
    for z in zones:
        lines.append("  " + html.escape(ZONE_LABEL.get(z, z)))
    if sl and tp:
        lines += ["", f"Level rencana: SL Rp{sl:,.0f} (1xATR) · TP "
                  f"Rp{tp:,.0f} (2xATR)".replace(",", ".")]
    lines += ["", "<i>Ini titik perhatian, bukan instruksi beli. Cek dulu "
              "/cek &lt;KODE&gt; sebelum menekan beli.</i>"]
    return "\n".join(lines)


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

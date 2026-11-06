"""PowerScore — menggabungkan tiga pin analisis jadi satu penilaian:

  50% teknikal (sinyal harga/MA/MACD/RSI — sudah di-backtest)   [core.analysis]
  30% fundamental (valuasi, profitabilitas, dividen, DER)        [core.fundamental]
  20% sentimen berita (lexicon Indonesia, decay 7 hari)          [core.news]

Catatan kejujuran: backtest hanya bisa memvalidasi bagian teknikal (berita
historis sulit diputar ulang). Fundamental & sentimen dinilai sebagai
kondisi SAAT INI, bukan prediksi."""

from __future__ import annotations

W_TECH, W_FUND, W_NEWS = 0.5, 0.3, 0.2


def combine(tech_pct: float, fund_pct: float | None, news_pct: float | None) -> dict:
    comp = tech_pct * W_TECH
    if fund_pct is not None:
        comp += fund_pct * W_FUND
    if news_pct is not None:
        comp += news_pct * W_NEWS
    comp = max(-100.0, min(100.0, comp))

    if comp >= 50:
        rating, emoji = "STRONG BUY", "🚀"
    elif comp >= 20:
        rating, emoji = "BUY", "📈"
    elif comp > -20:
        rating, emoji = "NETRAL", "😐"
    elif comp > -50:
        rating, emoji = "SELL", "📉"
    else:
        rating, emoji = "STRONG SELL", "🔻"
    return {"pct": round(comp), "rating": rating, "emoji": emoji}


def trap_warning(ret20d: float, ret1d: float) -> str | None:
    """Peringatan jebakan momentum — hasil temuan backtest (kasus ANTM -23%)."""
    if ret20d is not None and ret20d > 15 and ret1d > 0:
        return ("⚠️ <b>Waspada jebakan momentum</b>: harga sudah naik "
                f"{ret20d:+.0f}% dalam 20 hari. Hasil backtest menunjukkan "
                "sinyal BUY pasca-lonjakan lebar sering tiba di puncak "
                "(contoh: ANTM Sep 2026 → -23,65%). Tunggu konsolidasi dulu.")
    return None


def judge(tech_pct: float, fund: dict | None, news_sent: dict | None,
          snap: dict) -> str:
    """Gabungan teks penilaian tiga pin (dipakai /analisis)."""
    fund_pct = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = combine(tech_pct, fund_pct, news_pct)

    lines = [
        f"⚡ <b>POWERSCORE: {pw['pct']:+d}% → {pw['emoji']} {pw['rating']}</b>",
        f"  Teknikal {tech_pct:+.0f}% × 50% | Fundamental "
        f"{('%+.0f%%' % fund_pct) if fund_pct is not None else '—'} × 30% | "
        f"Berita {('%+.0f%%' % news_pct) if news_pct is not None else '—'} × 20%",
    ]
    trap = trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    if trap:
        lines.append(trap)
    return "\n".join(lines)

"""PowerScore — menggabungkan tiga pin analisis jadi satu penilaian:

  50% teknikal (sinyal harga/MA/MACD/RSI — sudah di-backtest)   [core.analysis]
  30% fundamental (valuasi, profitabilitas, dividen, DER)        [core.fundamental]
  20% sentimen berita (lexicon Indonesia, decay 7 hari)          [core.news]

Catatan kejujuran: backtest hanya bisa memvalidasi bagian teknikal (berita
historis sulit diputar ulang). Fundamental & sentimen dinilai sebagai
kondisi SAAT INI, bukan prediksi."""

from __future__ import annotations

from core import analysis

W_TECH, W_FUND, W_NEWS = 0.5, 0.3, 0.2
TREND_LABELS = analysis.TREND_LABELS
SCORE_DISCLAIMER = analysis.SCORE_DISCLAIMER


def combine(
    tech_pct: float,
    fund_pct: float | None,
    news_pct: float | None,
    *,
    forensic_penalty: float = 0.0,
) -> dict:
    """Gabungkan komponen yang tersedia dengan bobot dinormalisasi.

    Kalau salah satu sumber gagal diambil, bobotnya dibagi ke komponen yang ada
    — bukan dianggap nol (dulu komponen hilang membuat skor maksimal 50%
    sehingga STRONG BUY praktis mustahil). Bila ``forensic_penalty > 0``
    disuplai, penalti risiko arus kas/neraca diturunkan secara proporsional
    terhadap bobot fundamental dan dicatat di ``components``.
    """
    raw_weights = [("tech", tech_pct, W_TECH)]
    if fund_pct is not None:
        raw_weights.append(("fund", fund_pct, W_FUND))
    if news_pct is not None:
        raw_weights.append(("news", news_pct, W_NEWS))
    total_w = sum(w for _, _, w in raw_weights)
    weights = {
        name: round(w / total_w, 4) if total_w else 0.0
        for name, _, w in raw_weights
    }
    raw_comp = sum(v * w for _, v, w in raw_weights) / total_w if total_w else 0.0
    pen = max(0.0, float(forensic_penalty or 0.0))
    eff_fund_w = weights.get("fund", W_FUND)
    pen_impact = pen * eff_fund_w if pen > 0 else 0.0
    comp = max(-100.0, min(100.0, raw_comp - pen_impact))

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
    return {
        "pct": round(comp),
        "rating": rating,
        "trend_label": analysis.describe_rating(rating),
        "disclaimer": SCORE_DISCLAIMER,
        "emoji": emoji,
        "forensic_penalty": round(pen, 1) if pen else 0,
        "components": {
            "tech_pct": tech_pct,
            "fund_pct": fund_pct,
            "news_pct": news_pct,
            "weights": weights,
            "forensic_penalty": round(pen, 1) if pen else 0,
            "forensic_impact": -round(pen_impact, 2) if pen_impact else 0.0,
        },
    }


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
    forensic_penalty = float((fund or {}).get("forensic_penalty") or 0.0)
    if fund and "base_pct" in fund and forensic_penalty > 0:
        # `fund["pct"]` sudah memotong penalti forensik: hitung dari `base_pct`
        # + penalti terpisah agar transparansi komponen utuh tanpa double-count.
        fund_input = fund["base_pct"]
    else:
        fund_input = fund["pct"] if fund else None
    fund_display = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = combine(tech_pct, fund_input, news_pct, forensic_penalty=forensic_penalty)
    w = pw["components"]["weights"]
    w_t = w.get("tech", 0.0) * 100
    w_f = w.get("fund", 0.0) * 100
    w_n = w.get("news", 0.0) * 100

    lines = [
        f"⚡ <b>POWERSCORE: {pw['pct']:+d}% → {pw['emoji']} {pw['trend_label']}</b>",
        f"  Teknikal {tech_pct:+.0f}% × {w_t:.0f}% | Fundamental "
        f"{('%+.0f%%' % fund_display) if fund_display is not None else '—'} × {w_f:.0f}% | "
        f"Berita {('%+.0f%%' % news_pct) if news_pct is not None else '—'} × {w_n:.0f}%",
    ]
    if forensic_penalty > 0:
        flags = (fund or {}).get("forensic_flags") or []
        flag_txt = f" ({', '.join(flags[:3])})" if flags else ""
        lines.append(
            f"  ⚠️ <i>Penalti forensik fundamental: -{forensic_penalty:.0f} poin"
            f"{flag_txt} → dampak {pw['components']['forensic_impact']:+.1f}% ke PowerScore</i>"
        )
    missing = [n for n, v in (("fundamental", fund_display), ("berita", news_pct)) if v is None]
    if missing:
        lines.append(f"  <i>Komponen {', '.join(missing)} tidak tersedia — "
                     "bobot dinormalisasi ke komponen yang ada.</i>")
    lines.append(f"  ℹ️ <i>{pw['disclaimer']}</i>")
    trap = trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    if trap:
        lines.append(trap)
    return "\n".join(lines)

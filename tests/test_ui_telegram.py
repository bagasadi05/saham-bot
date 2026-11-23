"""Unit test untuk peningkatan tampilan Telegram: blockquote expandable, mode compact, heading bold, dan BOT_DISPLAY_NAME."""
from __future__ import annotations

import config
from core import analysis, reports
from handlers import menu


def test_build_report_blockquote_expandable_dan_mode_compact(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    full_txt = reports.build_report(res, None, None, {"ret20d": 1.0, "ret1y": 5.0}, compact=False)
    compact_txt = reports.build_report(res, None, None, {"ret20d": 1.0, "ret1y": 5.0}, compact=True)

    assert "<blockquote expandable>" in full_txt
    assert "📐 <b>Indikator Utama &amp; Level Kunci</b>" in full_txt
    assert "<pre>" in full_txt
    assert "🔬 <b>Konfirmasi Indikator Pro</b>" in full_txt
    assert len(compact_txt) < len(full_txt)


def test_menu_text_menghormati_bot_display_name(monkeypatch):
    monkeypatch.setattr(config, "BOT_DISPLAY_NAME", "SamBoyBot")
    assert "SamBoyBot" in menu.menu_text("pemula")
    assert "SamBoyBot" in menu.menu_text("expert")

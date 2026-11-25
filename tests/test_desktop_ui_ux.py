"""Tes offline untuk helper tampilan murni UI/UX aplikasi desktop (`desktop.py`).

Semua tes berjalan tanpa display (tidak membuat Tk) dan tanpa jaringan.
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import desktop
from core import marketcal


# ---------------------------------------------------------------------------
# desktop.py: helper tampilan murni (akan diisi setelah helper diimplementasi)
# ---------------------------------------------------------------------------

def test_desktop_pure_helpers():
    assert desktop.spinner_frame(0) == desktop.SPINNER_FRAMES[0]
    assert desktop.spinner_frame(3 + len(desktop.SPINNER_FRAMES)) == desktop.SPINNER_FRAMES[3]
    assert desktop.countdown_text(0) == "00:00"
    assert desktop.countdown_text(65) == "01:05"
    assert desktop.countdown_text(3661) == "1:01:01"
    assert desktop.countdown_text(-5) == "00:00"
    assert desktop.tab_label("⭐ Watchlist", 5) == "⭐ Watchlist (5)"
    assert desktop.tab_label("⭐ Watchlist", None) == "⭐ Watchlist"


def test_market_clock_text_open_closed_and_weekend():
    day = marketcal.last_trading_day(dt.date(2026, 11, 25))
    open_dt = dt.datetime(day.year, day.month, day.day, 10, 0, 5, tzinfo=marketcal.WIB)
    text, status = desktop.market_clock_text(open_dt)
    assert "10:00:05" in text and status == "open"

    closed_dt = dt.datetime(day.year, day.month, day.day, 20, 30, tzinfo=marketcal.WIB)
    text2, status2 = desktop.market_clock_text(closed_dt)
    assert "20:30:00" in text2 and status2 == "closed"

    # Minggu selalu tutup walau jam sesi
    sunday = dt.date(2026, 11, 29)
    assert sunday.weekday() == 6
    _, status3 = desktop.market_clock_text(dt.datetime(2026, 11, 29, 10, 0, tzinfo=marketcal.WIB))
    assert status3 == "closed"


# ---------------------------------------------------------------------------
# Audit statis: semua tombol di desktop.py wajib punya command yang valid
# ---------------------------------------------------------------------------

def test_semua_tombol_terhubung_command_yang_ada():
    """Parse AST desktop.py: setiap ttk.Button/tk.Button wajib punya ``command=``
    yang merujuk ke method/attr/fungsi yang benar-benar ada (anti tombol mati)."""
    src = Path(desktop.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    all_funcs: set[str] = set()
    app_methods: set[str] = set()
    app_attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            all_funcs.add(node.name)
        if isinstance(node, ast.ClassDef) and node.name == "SahamBotDesktopApp":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    app_methods.add(item.name)
                if isinstance(item, ast.Assign):
                    for tgt in item.targets:
                        if (
                            isinstance(tgt, ast.Attribute)
                            and isinstance(tgt.value, ast.Name)
                            and tgt.value.id == "self"
                        ):
                            app_attrs.add(tgt.attr)

    known = all_funcs | app_methods | app_attrs
    button_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Button"
    ]
    assert len(button_calls) >= 30, "audit harus mencakup semua tombol utama"

    for call in button_calls:
        cmd_kw = next((k for k in call.keywords if k.arg == "command"), None)
        assert cmd_kw is not None, f"tombol tanpa command di baris {call.lineno}"
        # setiap self.X yang dirujuk di dalam command harus ada
        for sub in ast.walk(cmd_kw.value):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            ):
                assert sub.attr in known, (
                    f"command merujuk self.{sub.attr} yang tidak ada (baris {call.lineno})"
                )
        # Name langsung sebagai command (mis. fungsi lokal _save) harus terdefinisi;
        # Name lain (mis. variabel lokal dlg pada dlg.destroy) wajar diabaikan.
        if isinstance(cmd_kw.value, ast.Name):
            name = cmd_kw.value.id
            assert name in known or name in dir(desktop), (
                f"command merujuk {name} yang tidak ada (baris {call.lineno})"
            )


# ---------------------------------------------------------------------------
# Helper UI/UX baru: teks rapi, loading tombol, cross-link antar fitur
# ---------------------------------------------------------------------------

def test_classify_line_hierarki_visual():
    assert desktop.classify_line("=== RENCANA MANUAL ===") == "heading"
    assert desktop.classify_line("--- DIAGNOSTIK MAE/MFE ---") == "heading"
    assert desktop.classify_line("⛔ VETO VERDICT: PBV ≤ 0") == "bad"
    assert desktop.classify_line("Verdict: HINDARI") == "bad"
    assert desktop.classify_line("⚠ risiko ARB") == "warn"
    assert desktop.classify_line("Verdict: TUNGGU") == "warn"
    assert desktop.classify_line("PRO / PENDUKUNG:") == "good"
    assert desktop.classify_line("Verdict: LAYAK (7 poin)") == "good"
    assert desktop.classify_line("🔎 Sumber: Yahoo · bar terakhir 2026-11-25") == "muted"
    assert desktop.classify_line("data tidak tersedia") == "muted"
    assert desktop.classify_line("biasa saja") == "normal"
    assert desktop.classify_line("") == "normal"


def test_inline_segments_markup_b_i_dan_entitas():
    segs = desktop.inline_segments("<b>tebal</b> &amp; <i>miring</i>")
    assert segs == [("tebal", "b"), (" & ", None), ("miring", "i")]
    # tag tak dikenal dibuang, entitas yang di-escape tetap literal
    assert desktop.inline_segments("a <unknown>x</unknown> b") == [("a x b", None)]
    assert desktop.inline_segments("&lt;b&gt;bukan&lt;/b&gt;") == [("<b>bukan</b>", None)]
    assert desktop.inline_segments("") == []


def test_busy_text_menunjukkan_proses_berjalan():
    txt = desktop.busy_text("⚡ Scan BEI (~944)", 4)
    assert txt.startswith(desktop.SPINNER_FRAMES[4])
    assert "⚡ Scan BEI (~944)" in txt and txt.endswith("…")


def test_hit_test_rects_dan_match_sector_option():
    rects = [(0.0, 0.0, 100.0, 20.0, "A"), (0.0, 20.0, 100.0, 40.0, "B")]
    assert desktop.hit_test_rects(rects, 50, 10) == "A"
    assert desktop.hit_test_rects(rects, 50, 30) == "B"
    assert desktop.hit_test_rects(rects, 50, 45) is None
    opts = ["Semua Sektor", "Keuangan", "Energi", "Teknologi"]
    assert desktop.match_sector_option("Keuangan", opts) == "Keuangan"
    assert desktop.match_sector_option("keuangan", opts) == "Keuangan"
    # alias IDX-IC (Inggris) -> nama sektor Indonesia di snapshot /scan
    assert desktop.match_sector_option("Financials", opts) == "Keuangan"
    assert desktop.match_sector_option("Technology", opts) == "Teknologi"
    assert desktop.match_sector_option("Energy", opts) == "Energi"
    assert desktop.match_sector_option("ZZZ", opts) is None
    assert desktop.match_sector_option("", opts) is None

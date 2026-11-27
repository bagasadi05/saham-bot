"""Tes offline untuk helper tampilan murni UI/UX aplikasi desktop (`core/desktop_helpers.py` & `desktop_ui/`).

Semua tes berjalan tanpa display (tidak membuat Tk) dan tanpa `tkinter` (CI headless).
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

from core import desktop_helpers as dh
from core import marketcal

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# core/desktop_helpers.py: helper tampilan murni (headless, tanpa tkinter)
# ---------------------------------------------------------------------------

def test_desktop_pure_helpers():
    assert dh.spinner_frame(0) == dh.SPINNER_FRAMES[0]
    assert dh.spinner_frame(3 + len(dh.SPINNER_FRAMES)) == dh.SPINNER_FRAMES[3]
    assert dh.countdown_text(0) == "00:00"
    assert dh.countdown_text(65) == "01:05"
    assert dh.countdown_text(3661) == "1:01:01"
    assert dh.countdown_text(-5) == "00:00"
    assert dh.tab_label("⭐ Watchlist", 5) == "⭐ Watchlist (5)"
    assert dh.tab_label("⭐ Watchlist", None) == "⭐ Watchlist"


def test_market_clock_text_open_closed_and_weekend():
    day = marketcal.last_trading_day(dt.date(2026, 11, 25))
    open_dt = dt.datetime(day.year, day.month, day.day, 10, 0, 5, tzinfo=marketcal.WIB)
    text, status = dh.market_clock_text(open_dt)
    assert "10:00:05" in text and status == "open"

    closed_dt = dt.datetime(day.year, day.month, day.day, 20, 30, tzinfo=marketcal.WIB)
    text2, status2 = dh.market_clock_text(closed_dt)
    assert "20:30:00" in text2 and status2 == "closed"

    # Minggu selalu tutup walau jam sesi
    sunday = dt.date(2026, 11, 29)
    assert sunday.weekday() == 6
    _, status3 = dh.market_clock_text(dt.datetime(2026, 11, 29, 10, 0, tzinfo=marketcal.WIB))
    assert status3 == "closed"


# ---------------------------------------------------------------------------
# Audit statis: semua tombol di desktop.py & desktop_ui/*.py wajib punya command valid
# ---------------------------------------------------------------------------

def test_semua_tombol_terhubung_command_yang_ada():
    """Parse AST desktop.py + desktop_ui/*.py: setiap ttk.Button/tk.Button wajib punya
    ``command=`` yang merujuk ke method/attr/fungsi yang benar-benar ada (anti tombol mati)."""
    files = [_REPO_ROOT / "desktop.py"] + sorted((_REPO_ROOT / "desktop_ui").glob("*.py"))
    all_funcs: set[str] = set()
    app_methods: set[str] = set()
    app_attrs: set[str] = set()
    imported_names: set[str] = set()
    button_entries: list[tuple[Path, ast.Call]] = []

    for fpath in files:
        src = fpath.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(fpath))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                all_funcs.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ClassDef) and (
                node.name == "SahamBotDesktopApp" or node.name.endswith("Mixin")
            ):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        app_methods.add(item.name)
                    for sub in ast.walk(item):
                        if isinstance(sub, ast.Assign):
                            for tgt in sub.targets:
                                if (
                                    isinstance(tgt, ast.Attribute)
                                    and isinstance(tgt.value, ast.Name)
                                    and tgt.value.id == "self"
                                ):
                                    app_attrs.add(tgt.attr)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Button"
            ):
                button_entries.append((fpath, node))

    known = all_funcs | app_methods | app_attrs
    assert len(button_entries) >= 30, "audit harus mencakup semua tombol utama"

    for fpath, call in button_entries:
        cmd_kw = next((k for k in call.keywords if k.arg == "command"), None)
        assert cmd_kw is not None, f"tombol tanpa command di {fpath.name}:{call.lineno}"
        for sub in ast.walk(cmd_kw.value):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            ):
                assert sub.attr in known, (
                    f"command merujuk self.{sub.attr} yang tidak ada ({fpath.name}:{call.lineno})"
                )
        if isinstance(cmd_kw.value, ast.Name):
            name = cmd_kw.value.id
            assert name in known or name in imported_names or hasattr(dh, name), (
                f"command merujuk {name} yang tidak ada ({fpath.name}:{call.lineno})"
            )


# ---------------------------------------------------------------------------
# Helper UI/UX baru: teks rapi, loading tombol, cross-link antar fitur
# ---------------------------------------------------------------------------

def test_classify_line_hierarki_visual():
    assert dh.classify_line("=== RENCANA MANUAL ===") == "heading"
    assert dh.classify_line("--- DIAGNOSTIK MAE/MFE ---") == "heading"
    assert dh.classify_line("⛔ VETO VERDICT: PBV ≤ 0") == "bad"
    assert dh.classify_line("Verdict: HINDARI") == "bad"
    assert dh.classify_line("⚠ risiko ARB") == "warn"
    assert dh.classify_line("Verdict: TUNGGU") == "warn"
    assert dh.classify_line("PRO / PENDUKUNG:") == "good"
    assert dh.classify_line("Verdict: LAYAK (7 poin)") == "good"
    assert dh.classify_line("🔎 Sumber: Yahoo · bar terakhir 2026-11-25") == "muted"
    assert dh.classify_line("data tidak tersedia") == "muted"
    assert dh.classify_line("biasa saja") == "normal"
    assert dh.classify_line("") == "normal"


def test_inline_segments_markup_b_i_dan_entitas():
    segs = dh.inline_segments("<b>tebal</b> &amp; <i>miring</i>")
    assert segs == [("tebal", "b"), (" & ", None), ("miring", "i")]
    # tag tak dikenal dibuang, entitas yang di-escape tetap literal
    assert dh.inline_segments("a <unknown>x</unknown> b") == [("a x b", None)]
    assert dh.inline_segments("&lt;b&gt;bukan&lt;/b&gt;") == [("<b>bukan</b>", None)]
    assert dh.inline_segments("") == []


def test_busy_text_menunjukkan_proses_berjalan():
    txt = dh.busy_text("⚡ Scan BEI (~944)", 4)
    assert txt.startswith(dh.SPINNER_FRAMES[4])
    assert "⚡ Scan BEI (~944)" in txt and txt.endswith("…")


def test_hit_test_rects_dan_match_sector_option():
    rects = [(0.0, 0.0, 100.0, 20.0, "A"), (0.0, 20.0, 100.0, 40.0, "B")]
    assert dh.hit_test_rects(rects, 50, 10) == "A"
    assert dh.hit_test_rects(rects, 50, 30) == "B"
    assert dh.hit_test_rects(rects, 50, 45) is None
    opts = ["Semua Sektor", "Keuangan", "Energi", "Teknologi"]
    assert dh.match_sector_option("Keuangan", opts) == "Keuangan"
    assert dh.match_sector_option("keuangan", opts) == "Keuangan"
    # alias IDX-IC (Inggris) -> nama sektor Indonesia di snapshot /scan
    assert dh.match_sector_option("Financials", opts) == "Keuangan"
    assert dh.match_sector_option("Technology", opts) == "Teknologi"
    assert dh.match_sector_option("Energy", opts) == "Energi"
    assert dh.match_sector_option("ZZZ", opts) is None
    assert dh.match_sector_option("", opts) is None


def test_safe_int_tidak_pernah_melempar():
    assert dh._safe_int("5") == 5
    assert dh._safe_int(3.9) == 3
    assert dh._safe_int("a") == 0
    assert dh._safe_int(None) == 0
    assert dh._safe_int(None, 7) == 7
    assert dh._safe_int(float("nan"), 2) == 2


def test_detect_action_alerts_aman_terhadap_baris_rusak():
    from core import desktop_monitor as dm

    prev: dict[str, str] = {}
    # baris tanpa ticker / dengan nilai aneh tidak boleh membuat crash
    out = dm.detect_action_alerts(prev, [{}], [{"ticker": "BBCA", "thesis": "GUGUR", "data_ok": False}])
    assert isinstance(out, list)
    out2 = dm.detect_action_alerts(prev, [{"ticker": "BBCA", "action": "BELI", "price": "abc"}], [])
    assert isinstance(out2, list) and out2 and out2[0]["ticker"] == "BBCA"


def test_format_plan_levels_rencana_beli():
    plan = {
        "ok": True,
        "entry": 9900, "entry_pullback": 9800, "sl": 9500, "sl_dist_pct": -3.94,
        "tp": 10500, "rr": 1.58, "lots": 2, "buy_cost": 1_980_000,
        "max_loss": 80_000, "max_loss_pct": 4.04,
    }
    lv = dh.format_plan_levels(plan)
    assert lv["entry"] == "9.900"
    assert lv["pullback"] == "9.800"
    assert lv["sl"] == "9.500 (-3.94%)"
    assert lv["tp"] == "10.500 (RR 1.58x)"
    assert lv["lots"] == "2"
    assert lv["cost"] == "Rp1.980.000"
    assert lv["risk"] == "Rp80.000 (4.04%)"
    # tanpa rencana valid -> semua "-" (jujur, bukan angka karang)
    for bad in (None, {}, {"ok": False}, "bukan dict"):
        assert set(dh.format_plan_levels(bad).values()) == {"-"}


def test_format_stale_age_and_news_line_tags():
    assert dh.format_stale_age(None) == ""
    assert dh.format_stale_age(20) == "baru saja"
    assert dh.format_stale_age(900) == "15 mnt lalu"
    assert dh.format_stale_age(3802.7789) == "1 jam 3 mnt lalu"
    assert dh.classify_line("💡 SARAN AKSI: Pasar sedang defensif") == "heading"
    assert dh.classify_line("  - [POSITIF] IHSG menguat") == "good"
    assert dh.classify_line("  - [NEGATIF] Asing lepas BBRI") == "bad"
    assert dh.classify_line("  - [NETRAL] IHSG dibuka datar") == "muted"


def test_compute_scan_sectors_fallback():
    from core import desktop_monitor as dm

    rows = [
        {"ticker": "BBCA", "sector": "Keuangan", "ret1d": 1.5},
        {"ticker": "BBRI", "sector": "Keuangan", "ret1d": 0.5},
        {"ticker": "BMRI", "sector": "Keuangan", "ret1d": 1.0},
        {"ticker": "ADRO", "sector": "Energi", "ret1d": -2.0},
        {"ticker": "PTBA", "sector": "Energi", "ret1d": -1.0},
        {"ticker": "ITMG", "sector": "Energi", "ret1d": -3.0},
    ]
    res = dm._compute_scan_sectors(rows)
    assert res["ok"] is True
    assert res["sectors"][0]["name"] == "Keuangan"
    assert res["sectors"][0]["pct"] == 1.0
    assert res["sectors"][1]["name"] == "Energi"
    assert res["sectors"][1]["pct"] == -2.0

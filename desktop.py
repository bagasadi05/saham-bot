"""SahamBot Desktop — terminal pemantauan & analisis saham BEI (offline-ready GUI).

Menyajikan 8 panel utama (Pasar & Rezim, Watchlist, Sinyal Pasar, Paket
Rekomendasi, Portofolio Asli, Jurnal & Evaluasi, Cek Emiten, Alert & Riwayat)
yang membaca mesin logika ``core/*`` dan SQLite ``store.db`` yang sama dengan
bot Telegram. Tanpa auto-trading atau penyimpanan kredensial broker: semua
eksekusi dilakukan secara manual oleh pengguna di Stockbit.
"""
from __future__ import annotations

import ctypes
import html
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, simpledialog, ttk

from PIL import Image, ImageDraw

try:
    from PIL import ImageTk
except Exception:  # pragma: no cover - environment headless tanpa Tcl/Tk PhotoImage
    ImageTk = None

try:
    import pystray
except ImportError:
    pystray = None

import config
from core import alerts as alertsmod
from core import chart as chartmod
from core import desktop_monitor as dm
from core import marketcal
from core import risk as riskmod
from core import store_portfolio
from core import store_profile
from core import store_watchlist
from core import textutil

log = logging.getLogger(__name__)

DESKTOP_CHAT_ID = dm.DEFAULT_PROFILE_ID
APP_ID = "SahamBot.Desktop.Terminal.2"
DEFAULT_MODAL_RP = 10_000_000
VALID_REFRESH_MIN = dict(store_profile.DESKTOP_REFRESH_OPTIONS)

# Palet UI/UX Financial Trading Terminal Dark (Slate + Tabular Mono)
THEME = {
    "bg": "#090d16",
    "bg_elev": "#0e1526",
    "card": "#131c31",
    "card_hover": "#1a2540",
    "border": "#24324d",
    "border_subtle": "#19243b",
    "fg": "#e2e8f0",
    "muted": "#94a3b8",
    "dim": "#64748b",
    "accent": "#38bdf8",
    "accent_dark": "#0284c7",
    "accent_soft": "#0c2d48",
    "green": "#22c55e",
    "green_soft": "#052e16",
    "red": "#ef4444",
    "red_soft": "#3b0a0a",
    "amber": "#f59e0b",
    "amber_soft": "#3a2502",
    "syariah": "#10b981",
    "row_even": "#0e1526",
    "row_odd": "#111a2e",
}

FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_SMALL_BOLD = ("Segoe UI", 9, "bold")
FONT_MONO = ("Consolas", 10)
FONT_MONO_BOLD = ("Consolas", 10, "bold")
FONT_KPI = ("Consolas", 15, "bold")

ALERT_KIND_ORDER = (
    "jump",
    "drop",
    "breakout",
    "breakdown",
    "oversold",
    "overbought",
    "ma_golden",
    "ma_death",
    "vol_spike",
)


# ---------------------------------------------------------------------------
# Formatter & Utilitas Dasar (teruji offline di tests/test_desktop_monitor.py)
# ---------------------------------------------------------------------------

def _fmt_rp(value: Any) -> str:
    """Format nominal Rupiah bersih (``Rp1.500.000`` / ``-Rp1.500.000`` / ``-``)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:  # NaN check
        return "-"
    sign = "-" if num < 0 else ""
    rounded = round(abs(num))
    formatted = f"{rounded:,}".replace(",", ".")
    return f"{sign}Rp{formatted}"


def _fmt_pct(value: Any, *, signed: bool = True) -> str:
    """Format persentase dengan 2 desimal (``+1.23%`` / ``-2.50%`` / ``-``)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if signed:
        return f"{num:+.2f}%"
    return f"{num:.2f}%"


def _fmt_num(value: Any, decimals: int = 0) -> str:
    """Format angka biasa dengan pemisah ribuan titik untuk angka non-Rupiah."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if decimals <= 0:
        return f"{round(num):,}".replace(",", ".")
    return f"{num:,.{decimals}f}".replace(",", ".")


def _strip_html(text: Any) -> str:
    """Bersihkan tag HTML sederhana dan decode entitas HTML."""
    raw = str(text or "")
    unescaped = html.unescape(raw)
    cleaned = re.sub(r"<[^>]+>", "", unescaped)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _fmt_arrow_pct(value: Any) -> str:
    """Format persen dengan penanda arah ``▲``/``▼`` untuk aksesibilitas visual."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if num > 0:
        return f"▲ +{num:.2f}%"
    if num < 0:
        return f"▼ {num:.2f}%"
    return "0.00%"


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        num = float(value)
        return num if num == num else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Helper tampilan murni (UI/UX): jam bursa, spinner, countdown, badge tab
# ---------------------------------------------------------------------------

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def spinner_frame(i: int) -> str:
    """Frame animasi spinner untuk indikator proses berjalan di status bar."""
    return SPINNER_FRAMES[int(i) % len(SPINNER_FRAMES)]


def countdown_text(seconds: float) -> str:
    """Format sisa waktu auto-refresh ``MM:SS`` (atau ``H:MM:SS`` bila > 1 jam)."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def tab_label(base: str, count: int | None) -> str:
    """Label tab dengan badge counter, mis. ``⭐ Watchlist (5)``."""
    return f"{base} ({count})" if count is not None else base


def market_clock_text(now: datetime | None = None) -> tuple[str, str]:
    """Teks jam WIB + status bursa; status ``open``/``closed`` untuk pewarnaan UI."""
    n = now or marketcal.now_wib()
    status = "open" if marketcal.market_open(n) else "closed"
    label = "BURSA BUKA" if status == "open" else "BURSA TUTUP"
    return f"{n:%H:%M:%S} WIB · {label}", status


def busy_text(base: str, frame: int) -> str:
    """Label tombol saat prosesnya berjalan: spinner + teks asli + elipsis."""
    return f"{spinner_frame(frame)} {base}…"


def classify_line(line: str) -> str:
    """Klasifikasi satu baris teks penjelasan menjadi tag gaya agar enak dibaca.

    Mengembalikan nama tag: ``heading``, ``good``, ``bad``, ``warn``, ``muted``,
    atau ``normal``.
    """
    s = str(line or "").strip()
    if not s:
        return "normal"
    if s.startswith("===") or (s.startswith("---") and s.endswith("---")):
        return "heading"
    up = s.upper()
    if "⛔" in s or "VETO" in up or "HINDARI" in up:
        return "bad"
    if "⚠" in s or "TUNGGU" in up or "STALE" in up:
        return "warn"
    if "RISIKO" in up or "CATATAN KRITIS" in up:
        return "bad"
    if s.startswith("PRO") or "LAYAK" in up or "PENDUKUNG" in up:
        return "good"
    if s.startswith("🔎") or "TIDAK TERSEDIA" in up:
        return "muted"
    return "normal"


def inline_segments(line: str) -> list[tuple[str, str | None]]:
    """Pecah satu baris menjadi segmen ``(teks, 'b'|'i'|None)`` dari markup ``<b>``/``<i>``.

    Tag lain dihilangkan, entitas HTML di-decode — aman untuk teks dari core.
    """
    out: list[tuple[str, str | None]] = []
    pos = 0
    style: str | None = None
    for m in re.finditer(r"<(/?)([bi])>", str(line or "")):
        chunk = str(line)[pos:m.start()]
        if chunk:
            out.append((html.unescape(re.sub(r"<[^>]+>", "", chunk)), style))
        style = None if m.group(1) == "/" else m.group(2)
        pos = m.end()
    tail = str(line)[pos:]
    if tail:
        out.append((html.unescape(re.sub(r"<[^>]+>", "", tail)), style))
    return [(t, s) for t, s in out if t]


def hit_test_rects(
    rects: list[tuple[float, float, float, float, str]], x: float, y: float,
) -> str | None:
    """Kembalikan nama pada rect ``(x0, y0, x1, y1, nama)`` yang terkena titik ``(x, y)``."""
    for x0, y0, x1, y1, name in rects:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return None


# Alias nama sektor IDX-IC (Inggris, dipakai grafik rotasi) ke sektor Indonesia (snapshot /scan)
SECTOR_ALIAS_IDX_IC = {
    "financials": "keuangan",
    "energy": "energi",
    "basic materials": "bahan baku",
    "industrials": "industri",
    "consumer cyclicals": "konsumen siklikal",
    "consumer non-cyclicals": "konsumen primer",
    "technology": "teknologi",
    "infrastructures": "infrastruktur",
    "transportation & logistics": "transportasi",
    "transportation": "transportasi",
    "healthcare": "kesehatan",
    "properties & real estate": "properti",
    "real estate": "properti",
    "utilities": "utilitas",
}


def format_plan_levels(plan: Any) -> dict[str, str]:
    """Format level rencana beli (entry/SL/TP/lot/biaya/risiko) untuk kartu ringkasan.

    ``plan`` dengan ``ok=False``/bukan dict menghasilkan semua ``"-"`` (jujur: tidak ada angka).
    """
    keys = ("entry", "pullback", "sl", "tp", "lots", "cost", "risk")
    if not isinstance(plan, dict) or not plan.get("ok"):
        return {k: "-" for k in keys}
    return {
        "entry": _fmt_num(plan.get("entry"), 0),
        "pullback": _fmt_num(plan.get("entry_pullback"), 0),
        "sl": f"{_fmt_num(plan.get('sl'), 0)} ({_fmt_pct(plan.get('sl_dist_pct'))})",
        "tp": f"{_fmt_num(plan.get('tp'), 0)} (RR {float(plan.get('rr') or 0.0):.2f}x)",
        "lots": _fmt_num(plan.get("lots"), 0),
        "cost": _fmt_rp(plan.get("buy_cost")),
        "risk": f"{_fmt_rp(plan.get('max_loss'))} ({_fmt_pct(plan.get('max_loss_pct'), signed=False)})",
    }


def match_sector_option(name: str, options: list[str]) -> str | None:
    """Cocokkan nama sektor grafik IDX-IC ke opsi filter sinyal (alias, exact, lalu substring)."""
    n = str(name or "").strip()
    if not n:
        return None
    low = SECTOR_ALIAS_IDX_IC.get(n.lower(), n.lower())
    for opt in options:
        if opt == n or opt.lower() == low:
            return opt
    for opt in options:
        o = opt.lower()
        if low in o or o in low:
            return opt
    return None


def create_tray_icon_image(size: int = 64) -> Image.Image:
    """Buat ikon baki sistem SahamBot (ikon candlestick & garis tren hijau/biru)."""
    dim = max(16, int(size))
    img = Image.new("RGBA", (dim, dim), (9, 13, 22, 255))
    draw = ImageDraw.Draw(img)

    margin = max(2, dim // 12)
    radius = max(3, dim // 6)
    draw.rounded_rectangle(
        [margin, margin, dim - margin - 1, dim - margin - 1],
        radius=radius,
        fill=(19, 28, 49, 255),
        outline=(56, 189, 248, 255),
        width=max(1, dim // 24),
    )

    # Tiga candlestick naik + garis tren aksen sky-blue
    bars = [
        (0.26, 0.52, 0.72, (239, 68, 68, 255)),
        (0.49, 0.36, 0.62, (34, 197, 94, 255)),
        (0.72, 0.22, 0.50, (34, 197, 94, 255)),
    ]
    bar_w = max(2, int(dim * 0.12))
    wick_w = max(1, dim // 32)
    for cx_r, top_r, bot_r, color in bars:
        cx = int(dim * cx_r)
        top_y = int(dim * top_r)
        bot_y = int(dim * bot_r)
        draw.line([(cx, max(margin + 2, top_y - bar_w)), (cx, min(dim - margin - 2, bot_y + bar_w))],
                  fill=color, width=wick_w)
        draw.rectangle(
            [cx - bar_w // 2, top_y, cx + bar_w // 2, bot_y],
            fill=color,
        )

    line_w = max(1, dim // 20)
    draw.line(
        [(int(dim * 0.20), int(dim * 0.74)),
         (int(dim * 0.48), int(dim * 0.48)),
         (int(dim * 0.80), int(dim * 0.20))],
        fill=(56, 189, 248, 255),
        width=line_w,
    )
    return img


def _news_item_tag(item: dict[str, Any]) -> str:
    """Ambil label sentimen item berita (`POSITIF`/`NEGATIF`/`NETRAL`) dari skor `sent`."""
    explicit = str(item.get("sentiment") or item.get("label") or "").strip().upper()
    if explicit:
        return explicit
    s = _safe_float(item.get("sent"))
    if s is None:
        return "NETRAL"
    if s >= 0.1:
        return "POSITIF"
    if s <= -0.1:
        return "NEGATIF"
    return "NETRAL"


def send_windows_toast(title: str, body: str) -> bool:
    """Kirim notifikasi balon/toast Windows non-blocking via WinForms NotifyIcon."""
    if sys.platform != "win32":
        return False
    clean_t = _strip_html(title)[:80].replace("'", "''")
    clean_b = _strip_html(body)[:200].replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.BalloonTipIcon = 'Info';"
        f"$n.BalloonTipTitle = '{clean_t}';"
        f"$n.BalloonTipText = '{clean_b}';"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(4500);"
        "Start-Sleep -Milliseconds 4800;"
        "$n.Dispose()"
    )
    try:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return True
    except Exception:
        return False


def _enable_dark_titlebar(win: tk.Misc) -> None:
    """Aktifkan dark titlebar DWM Windows 10/11 agar serasi dengan tema terminal."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(1)
        for attr in (20, 19):
            res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val),
            )
            if res == 0:
                break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Custom Widget Toggle (Switch)
# ---------------------------------------------------------------------------

class Switch(tk.Canvas):
    """Toggle switch halus bergaya terminal finansial modern."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        variable: tk.BooleanVar | None = None,
        command: Callable[[], None] | None = None,
        on_color: str = THEME["accent"],
        off_color: str = "#2a3958",
        bg: str = THEME["bg_elev"],
        fg: str = THEME["fg"],
        font: tuple = FONT_SMALL_BOLD,
    ) -> None:
        self._var = variable or tk.BooleanVar(value=False)
        self._command = command
        self._on_color = on_color
        self._off_color = off_color
        self._text = text
        self._fg = fg
        self._font = font
        self._track_w = 40
        self._track_h = 20
        try:
            txt_w = tkfont.Font(master, font=font).measure(text) if text else 0
        except Exception:
            txt_w = len(text) * 8 if text else 0
        width = self._track_w + (16 + txt_w if text else 4)
        super().__init__(
            master,
            width=max(self._track_w, width),
            height=26,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.bind("<Button-1>", self._toggle)
        self._trace_id = self._var.trace_add("write", lambda *_: self._redraw())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._redraw()

    def _on_destroy(self, _event: tk.Event) -> None:
        try:
            self._var.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _toggle(self, _event: tk.Event | None = None) -> None:
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def get(self) -> bool:
        return bool(self._var.get())

    def set(self, val: bool) -> None:
        self._var.set(bool(val))

    def _redraw(self) -> None:
        self.delete("all")
        active = bool(self._var.get())
        bg_clr = self._on_color if active else self._off_color
        y0 = 3
        h = self._track_h
        w = self._track_w
        r = h // 2
        self.create_oval(2, y0, 2 + h, y0 + h, fill=bg_clr, outline=bg_clr)
        self.create_oval(w - h + 2, y0, w + 2, y0 + h, fill=bg_clr, outline=bg_clr)
        self.create_rectangle(2 + r, y0, w - r + 2, y0 + h, fill=bg_clr, outline=bg_clr)

        knob_d = h - 4
        kx = (w - knob_d) if active else 4
        self.create_oval(kx, y0 + 2, kx + knob_d, y0 + 2 + knob_d, fill="#f8fafc", outline="")

        if self._text:
            self.create_text(
                w + 10,
                13,
                text=self._text,
                anchor="w",
                fill=self._fg if active else THEME["muted"],
                font=self._font,
            )


# ---------------------------------------------------------------------------
# Pembantu Tabel, Kartu KPI & Dialog Modal
# ---------------------------------------------------------------------------

def _make_kpi_card(
    parent: tk.Misc,
    title: str,
    default_val: str = "-",
    default_sub: str = "",
) -> dict[str, Any]:
    """Bangun kartu KPI ringkas dengan angka tabular & keterangan sekunder."""
    frame = tk.Frame(
        parent,
        bg=THEME["card"],
        highlightbackground=THEME["border"],
        highlightthickness=1,
        padx=12,
        pady=9,
    )
    lbl_title = tk.Label(
        frame, text=title.upper(), bg=THEME["card"], fg=THEME["muted"],
        font=("Segoe UI", 8, "bold"), anchor="w",
    )
    lbl_title.pack(fill="x")
    lbl_val = tk.Label(
        frame, text=default_val, bg=THEME["card"], fg=THEME["fg"],
        font=FONT_KPI, anchor="w",
    )
    lbl_val.pack(fill="x", pady=(3, 1))
    lbl_sub = tk.Label(
        frame, text=default_sub, bg=THEME["card"], fg=THEME["dim"],
        font=FONT_SMALL, anchor="w",
    )
    lbl_sub.pack(fill="x")
    return {"frame": frame, "title": lbl_title, "val": lbl_val, "sub": lbl_sub}


def _set_kpi(
    card: dict[str, Any],
    val: str,
    sub: str = "",
    *,
    color: str = THEME["fg"],
    sub_color: str = THEME["muted"],
) -> None:
    card["val"].configure(text=val, fg=color)
    card["sub"].configure(text=sub, fg=sub_color)


def _parse_sort_key(raw: str, kind: str) -> tuple[int, Any]:
    """Konversi string sel Treeview menjadi kunci pengurutan yang konsisten."""
    txt = str(raw or "").strip()
    if txt in ("", "-", "NA", "tidak tersedia"):
        return (1, 0.0)
    if kind == "num":
        cleaned = (
            txt.replace("▲", "")
            .replace("▼", "")
            .replace("Rp", "")
            .replace("%", "")
            .replace("x", "")
            .replace("+", "")
            .replace(" ", "")
        )
        if cleaned.count(".") >= 1 and "," not in cleaned:
            parts = cleaned.split(".")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
                cleaned = "".join(parts)
        cleaned = cleaned.replace(",", ".")
        m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if m:
            try:
                return (0, float(m.group(0)))
            except ValueError:
                pass
        return (1, txt.lower())
    return (0, txt.lower())


def _sort_treeview(tree: ttk.Treeview, col: str, kind: str, state: dict[str, Any]) -> None:
    """Urutkan baris Treeview saat header diklik beserta indikator ``▲``/``▼``."""
    prev_col = state.get("col")
    desc = (not state.get("desc", False)) if prev_col == col else (kind == "num")
    state["col"] = col
    state["desc"] = desc

    items = [(tree.set(iid, col), iid) for iid in tree.get_children("")]
    if desc:
        valid = [it for it in items if _parse_sort_key(it[0], kind)[0] == 0]
        missing = [it for it in items if _parse_sort_key(it[0], kind)[0] != 0]
        valid.sort(key=lambda x: _parse_sort_key(x[0], kind)[1], reverse=True)
        ordered = valid + missing
    else:
        ordered = sorted(items, key=lambda x: _parse_sort_key(x[0], kind))

    for idx, (_, iid) in enumerate(ordered):
        tree.move(iid, "", idx)
        tags = list(tree.item(iid, "tags") or ())
        semantic = [t for t in tags if t in ("buy", "sell", "warn", "info", "muted")]
        if semantic:
            tree.item(iid, tags=tuple(semantic))
        else:
            tree.item(iid, tags=("row_even" if idx % 2 == 0 else "row_odd",))

    headings: dict[str, str] = state.get("headings") or {}
    arrow = " ▼" if desc else " ▲"
    for c_name, base_text in headings.items():
        tree.heading(c_name, text=(base_text + arrow) if c_name == col else base_text)


def _configure_tree_tags(tree: ttk.Treeview) -> None:
    tree.tag_configure("row_even", background=THEME["row_even"], foreground=THEME["fg"])
    tree.tag_configure("row_odd", background=THEME["row_odd"], foreground=THEME["fg"])
    tree.tag_configure("buy", background=THEME["green_soft"], foreground=THEME["green"])
    tree.tag_configure("sell", background=THEME["red_soft"], foreground=THEME["red"])
    tree.tag_configure("warn", background=THEME["amber_soft"], foreground=THEME["amber"])
    tree.tag_configure("info", background=THEME["accent_soft"], foreground=THEME["accent"])
    tree.tag_configure("muted", foreground=THEME["muted"])


def _build_treeview(
    parent: tk.Misc,
    specs: list[tuple[str, str, int, str, str]],
    *,
    height: int = 12,
    on_double_click: Callable[[tk.Event], None] | None = None,
    on_right_click: Callable[[tk.Event], None] | None = None,
) -> ttk.Treeview:
    """Bangun Treeview standar dengan scrollbar Y/X, tag warna, dan click-to-sort.

    ``specs`` berisi tuple ``(id_kolom, judul, lebar, anchor, jenis_sort['num'|'str'])``.
    """
    wrap = tk.Frame(parent, bg=THEME["bg"])
    wrap.pack(fill="both", expand=True)

    cols = [s[0] for s in specs]
    tree = ttk.Treeview(wrap, columns=cols, show="headings", height=height, selectmode="browse")
    ysb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    xsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    ysb.grid(row=0, column=1, sticky="ns")
    xsb.grid(row=1, column=0, sticky="ew")
    wrap.rowconfigure(0, weight=1)
    wrap.columnconfigure(0, weight=1)

    sort_state: dict[str, Any] = {
        "col": None,
        "desc": False,
        "headings": {s[0]: s[1] for s in specs},
    }
    for cid, title, width, anchor, kind in specs:
        stretch = cid in ("reasons", "notes", "warnings", "guidance", "message", "sector")
        tree.heading(
            cid,
            text=title,
            anchor="w" if anchor == "w" else "center",
            command=lambda c=cid, k=kind: _sort_treeview(tree, c, k, sort_state),
        )
        tree.column(cid, width=width, minwidth=max(48, width // 2), anchor=anchor, stretch=stretch)

    _configure_tree_tags(tree)
    if on_double_click:
        tree.bind("<Double-1>", on_double_click)
    if on_right_click:
        tree.bind("<Button-3>", on_right_click)
    return tree


def _populate_tree(
    tree: ttk.Treeview,
    rows_with_tags: list[tuple[tuple[Any, ...], str | None]],
) -> None:
    """Isi ulang Treeview sambil menjaga keterbacaan striping baris."""
    tree.delete(*tree.get_children())
    for idx, (values, tone_tag) in enumerate(rows_with_tags):
        stripe = "row_even" if idx % 2 == 0 else "row_odd"
        tags = (tone_tag,) if tone_tag else (stripe,)
        tree.insert("", "end", values=values, tags=tags)


# ---------------------------------------------------------------------------
# Aplikasi Utama Desktop
# ---------------------------------------------------------------------------

class SahamBotDesktopApp:
    """Aplikasi GUI Windows & System Tray untuk pemantauan pasar BEI SahamBot."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SahamBot Desktop · Terminal Analisis BEI")
        self.root.configure(bg=THEME["bg"])
        self.root.minsize(1080, 680)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = min(1400, max(1080, int(sw * 0.88)))
        win_h = min(880, max(680, int(sh * 0.86)))
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy_flags: dict[str, bool] = {}
        self._pending_jobs: dict[str, tuple[Callable[[], Any], str]] = {}
        self._prev_alert_state: dict[str, str] = {}
        self._last_market_pulse: dict[str, Any] = {}
        self._last_sinyal_rows: list[dict[str, Any]] = []
        self._last_paket_res: dict[str, Any] = {}
        self._last_watch_rows: list[dict[str, Any]] = []
        self._last_port_rows: list[dict[str, Any]] = []
        self._last_port_summary: dict[str, Any] = {}
        self._last_cek_detail: dict[str, Any] = {}
        self._last_jurnal_res: dict[str, Any] = {}
        self._alert_history: list[dict[str, Any]] = []
        self._seen_alert_keys: set[str] = set()
        self._chart_photo: Any = None
        self._tray_icon: Any = None
        self._closing = False
        self._pump_after_id: str | None = None
        self._auto_after_id: str | None = None
        self._chart_after_id: str | None = None
        self._clock_after_id: str | None = None
        self._status_after_id: str | None = None
        self._status_tick = 0
        self._last_update_ts = ""
        self._next_auto_ts: float | None = None
        self._ui_state: dict[str, Any] = {}
        self._recent_tickers: list[str] = []
        self._busy_buttons: dict[str, list[Any]] = {}
        self._busy_keys: set[str] = set()
        self._global_busy_buttons: list[Any] = []
        self._sector_rects: list[tuple[float, float, float, float, str]] = []

        # Variabel UI persisten (mendukung profil "desktop" maupun chat_id Telegram)
        self.var_profile = tk.StringVar(value=str(DESKTOP_CHAT_ID))
        cid = self._current_chat_id()
        self._active_profile = cid
        # State sesi UI terakhir (geometri, tab aktif, auto-refresh, toast, zoom, riwayat cek)
        self._ui_state = store_profile.get_desktop_ui_state(cid)
        self._recent_tickers = list(self._ui_state.get("recent_tickers") or [])
        saved_modal = store_watchlist.get_modal(cid) or DEFAULT_MODAL_RP
        saved_syariah = bool(store_profile.get_syariah_mode(cid))
        self.var_modal = tk.StringVar(value=str(int(saved_modal)))
        self.var_syariah = tk.BooleanVar(value=saved_syariah)
        self.var_toast = tk.BooleanVar(value=bool(self._ui_state.get("toast", True)))
        self.var_refresh_interval = tk.StringVar(
            value=str(self._ui_state.get("refresh") or "15m")
        )
        self.var_status = tk.StringVar(value="Menyiapkan data pasar BEI...")
        self.var_regime_badge = tk.StringVar(value="IHSG: memuat...")

        # Filter & input per tab
        self.var_watch_add = tk.StringVar()
        self.var_watch_search = tk.StringVar()
        self.var_sinyal_filter = tk.StringVar(value="ALL")
        self.var_sinyal_search = tk.StringVar()
        self.var_sinyal_sector = tk.StringVar(value="Semua Sektor")
        self.var_sinyal_quality = tk.BooleanVar(value=False)
        self.var_paket_max = tk.StringVar(value="5")
        self.var_port_ticker = tk.StringVar()
        self.var_port_side = tk.StringVar(value="BELI")
        self.var_port_price = tk.StringVar()
        self.var_port_lots = tk.StringVar(value="1")
        self.var_port_notes = tk.StringVar()
        self.var_jurnal_limit = tk.StringVar(value="50")
        self.var_cek_ticker = tk.StringVar(value="BBCA")
        self.var_cek_period = tk.StringVar(value="1y")
        self.var_alert_filter = tk.StringVar(value="Semua")

        self._setup_style()
        self._set_window_icon()
        self._build_layout()
        self._bind_shortcuts()
        _enable_dark_titlebar(self.root)

        # Pulihkan state sesi terakhir: geometri jendela, tab aktif, skala font
        geo = str(self._ui_state.get("geometry") or "")
        if geo:
            try:
                self.root.geometry(geo)
            except Exception:
                pass
        try:
            self.notebook.select(int(self._ui_state.get("tab_index") or 0))
        except Exception:
            pass
        self._apply_font_scale(self._ui_state.get("font_scale", 1.0), persist=False)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close_or_tray)
        self._pump_after_id = self.root.after(120, self._pump_queue)
        self._clock_after_id = self.root.after(500, self._tick_clock)
        self._status_after_id = self.root.after(600, self._tick_statusbar)
        self.root.after(200, self.refresh_all)
        self._schedule_auto_refresh()
        self._setup_tray()

    # ------------------------------------------------------------------
    # Tema & Gaya TTK
    # ------------------------------------------------------------------

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=THEME["bg"], foreground=THEME["fg"], font=FONT_UI)
        style.configure("TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["card"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["fg"], font=FONT_UI)

        style.configure(
            "TNotebook",
            background=THEME["bg_elev"],
            borderwidth=0,
            tabmargins=[4, 6, 4, 0],
        )
        style.configure(
            "TNotebook.Tab",
            background=THEME["card"],
            foreground=THEME["muted"],
            padding=[14, 8],
            borderwidth=0,
            font=FONT_SMALL_BOLD,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", THEME["accent_soft"]), ("active", THEME["card_hover"])],
            foreground=[("selected", THEME["accent"]), ("active", THEME["fg"])],
        )

        style.configure(
            "Treeview",
            background=THEME["bg_elev"],
            fieldbackground=THEME["bg_elev"],
            foreground=THEME["fg"],
            bordercolor=THEME["border"],
            borderwidth=1,
            rowheight=27,
            font=FONT_MONO,
        )
        style.configure(
            "Treeview.Heading",
            background=THEME["card"],
            foreground=THEME["muted"],
            bordercolor=THEME["border"],
            relief="flat",
            padding=[8, 6],
            font=FONT_SMALL_BOLD,
        )
        style.map(
            "Treeview.Heading",
            background=[("active", THEME["card_hover"])],
            foreground=[("active", THEME["fg"])],
        )
        style.map(
            "Treeview",
            background=[("selected", THEME["accent_soft"])],
            foreground=[("selected", THEME["accent"])],
        )

        style.configure(
            "Accent.TButton",
            background=THEME["accent_dark"],
            foreground="#ffffff",
            borderwidth=0,
            padding=[12, 6],
            font=FONT_SMALL_BOLD,
        )
        style.map(
            "Accent.TButton",
            background=[("active", THEME["accent"]), ("disabled", THEME["border"])],
        )

        style.configure(
            "Ghost.TButton",
            background=THEME["card"],
            foreground=THEME["fg"],
            bordercolor=THEME["border"],
            borderwidth=1,
            padding=[10, 5],
            font=FONT_SMALL_BOLD,
        )
        style.map(
            "Ghost.TButton",
            background=[("active", THEME["card_hover"])],
        )

        style.configure(
            "Danger.TButton",
            background=THEME["red_soft"],
            foreground=THEME["red"],
            bordercolor=THEME["red"],
            borderwidth=1,
            padding=[10, 5],
            font=FONT_SMALL_BOLD,
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#5c1111")],
        )

        style.configure(
            "Dark.TCombobox",
            fieldbackground=THEME["card"],
            background=THEME["card"],
            foreground=THEME["fg"],
            arrowcolor=THEME["accent"],
            bordercolor=THEME["border"],
        )
        self.root.option_add("*TCombobox*Listbox.background", THEME["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", THEME["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", THEME["accent_soft"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", THEME["accent"])

    def _set_window_icon(self) -> None:
        try:
            img = create_tray_icon_image(64)
            if ImageTk is not None:
                self._icon_photo = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _entry(self, parent: tk.Misc, var: tk.StringVar, width: int = 14) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=var,
            width=width,
            bg=THEME["card"],
            fg=THEME["fg"],
            insertbackground=THEME["accent"],
            relief="flat",
            highlightbackground=THEME["border"],
            highlightcolor=THEME["accent"],
            highlightthickness=1,
            font=FONT_MONO,
        )

    def _build_layout(self) -> None:
        # ---- Top Command Bar ----
        topbar = tk.Frame(
            self.root,
            bg=THEME["bg_elev"],
            highlightbackground=THEME["border"],
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        topbar.pack(fill="x")

        brand = tk.Frame(topbar, bg=THEME["bg_elev"])
        brand.pack(side="left")
        tk.Label(
            brand, text="SAHAMBOT", bg=THEME["bg_elev"], fg=THEME["accent"],
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            brand, text=" · Terminal BEI", bg=THEME["bg_elev"], fg=THEME["muted"],
            font=FONT_SMALL_BOLD,
        ).pack(side="left", padx=(2, 12))

        self.lbl_regime = tk.Label(
            topbar,
            textvariable=self.var_regime_badge,
            bg=THEME["card"],
            fg=THEME["fg"],
            padx=10,
            pady=4,
            font=FONT_SMALL_BOLD,
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.lbl_regime.pack(side="left", padx=(0, 12))

        # Jam WIB live + status bursa (BUKA/TUTUP)
        self.lbl_clock = tk.Label(
            topbar,
            text="--:--:-- WIB",
            bg=THEME["card"],
            fg=THEME["dim"],
            padx=10,
            pady=4,
            font=FONT_MONO_BOLD,
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.lbl_clock.pack(side="left", padx=(0, 12))

        # Kontrol kanan topbar
        right_bar = tk.Frame(topbar, bg=THEME["bg_elev"])
        right_bar.pack(side="right")

        tk.Label(
            right_bar, text="Profil:", bg=THEME["bg_elev"], fg=THEME["muted"], font=FONT_SMALL,
        ).pack(side="left", padx=(0, 3))
        ent_prof = self._entry(right_bar, self.var_profile, width=9)
        ent_prof.pack(side="left", padx=(0, 6))
        ent_prof.bind("<Return>", lambda _e: self._save_profile())

        tk.Label(
            right_bar, text="Modal:", bg=THEME["bg_elev"], fg=THEME["muted"], font=FONT_SMALL,
        ).pack(side="left", padx=(0, 3))
        ent_modal = self._entry(right_bar, self.var_modal, width=12)
        ent_modal.pack(side="left", padx=(0, 4))
        ent_modal.bind("<Return>", lambda _e: self._save_profile())
        ttk.Button(right_bar, text="Simpan", style="Ghost.TButton", command=self._save_profile).pack(
            side="left", padx=(0, 8),
        )

        self.sw_syariah = Switch(
            right_bar,
            text="Syariah (ISSI)",
            variable=self.var_syariah,
            command=self._on_toggle_syariah,
            on_color=THEME["syariah"],
            bg=THEME["bg_elev"],
        )
        self.sw_syariah.pack(side="left", padx=(0, 8))

        self.sw_toast = Switch(
            right_bar,
            text="Toast",
            variable=self.var_toast,
            command=lambda: self._save_ui_state(),
            on_color=THEME["accent"],
            bg=THEME["bg_elev"],
        )
        self.sw_toast.pack(side="left", padx=(0, 8))

        tk.Label(
            right_bar, text="Auto:", bg=THEME["bg_elev"], fg=THEME["muted"], font=FONT_SMALL,
        ).pack(side="left", padx=(0, 4))
        cb_auto = ttk.Combobox(
            right_bar,
            textvariable=self.var_refresh_interval,
            values=list(VALID_REFRESH_MIN.keys()),
            width=7,
            state="readonly",
            style="Dark.TCombobox",
        )
        cb_auto.pack(side="left", padx=(0, 8))
        cb_auto.bind(
            "<<ComboboxSelected>>",
            lambda _e: (self._schedule_auto_refresh(), self._save_ui_state()),
        )

        self.btn_refresh = ttk.Button(
            right_bar, text="↻ Refresh", style="Accent.TButton", command=self.refresh_active_tab,
        )
        self.btn_refresh.pack(side="left", padx=(0, 4))
        self.btn_all = ttk.Button(
            right_bar, text="Semua Tab", style="Ghost.TButton", command=self.refresh_all,
        )
        self.btn_all.pack(side="left")
        # Kedua tombol topbar ikut menunjukkan estado sibuk saat ada proses berjalan
        self._global_busy_buttons = [self.btn_refresh, self.btn_all]
        if pystray is not None:
            ttk.Button(
                right_bar, text="🗗 Ke Tray", style="Ghost.TButton",
                command=self._on_close_or_tray,
            ).pack(side="left", padx=(4, 0))

        # ---- Main Notebook (8 Tab) ----
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self.tab_market = ttk.Frame(self.notebook)
        self.tab_watch = ttk.Frame(self.notebook)
        self.tab_sinyal = ttk.Frame(self.notebook)
        self.tab_paket = ttk.Frame(self.notebook)
        self.tab_port = ttk.Frame(self.notebook)
        self.tab_jurnal = ttk.Frame(self.notebook)
        self.tab_cek = ttk.Frame(self.notebook)
        self.tab_alerts = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_market, text="📈 Pasar & Rezim")
        self.notebook.add(self.tab_watch, text="⭐ Watchlist")
        self.notebook.add(self.tab_sinyal, text="🎯 Sinyal Pasar")
        self.notebook.add(self.tab_paket, text="📦 Paket Beli")
        self.notebook.add(self.tab_port, text="💼 Portofolio Asli")
        self.notebook.add(self.tab_jurnal, text="📓 Jurnal & Evaluasi")
        self.notebook.add(self.tab_cek, text="🔍 Cek Emiten")
        self.notebook.add(self.tab_alerts, text="🔔 Alert (0)")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_market_tab()
        self._build_watch_tab()
        self._build_sinyal_tab()
        self._build_paket_tab()
        self._build_port_tab()
        self._build_jurnal_tab()
        self._build_cek_tab()
        self._build_alerts_tab()

        # ---- Footer Status & Disclaimer Jujur ----
        footer = tk.Frame(
            self.root,
            bg=THEME["bg_elev"],
            highlightbackground=THEME["border_subtle"],
            highlightthickness=1,
            padx=12,
            pady=5,
        )
        footer.pack(fill="x", side="bottom")
        tk.Label(
            footer,
            textvariable=self.var_status,
            bg=THEME["bg_elev"],
            fg=THEME["accent"],
            font=FONT_SMALL,
            anchor="w",
        ).pack(side="left")
        # Indikator proses berjalan + countdown auto-refresh + jam update terakhir
        self.lbl_status_extra = tk.Label(
            footer,
            text="",
            bg=THEME["bg_elev"],
            fg=THEME["dim"],
            font=FONT_SMALL,
            anchor="w",
        )
        self.lbl_status_extra.pack(side="left", padx=(14, 0))
        tk.Label(
            footer,
            text=(
                "Data ±15 menit delay · Tidak ada janji untung · "
                "Eksekusi manual di Stockbit (tanpa auto-trading) · "
                "Pintasan: F5/Ctrl+R Refresh | Ctrl+F Cari | Ctrl+K Cek Emiten | "
                "Ctrl+1..8 Tab | Ctrl +/- Zoom | Ctrl+0 Reset"
            ),
            bg=THEME["bg_elev"],
            fg=THEME["dim"],
            font=("Segoe UI", 8),
            anchor="e",
        ).pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F5>", lambda _e: self.refresh_active_tab())
        self.root.bind("<Control-r>", lambda _e: self.refresh_active_tab())
        self.root.bind("<Control-R>", lambda _e: self.refresh_active_tab())
        self.root.bind("<Control-k>", lambda _e: self._focus_cek_ticker())
        self.root.bind("<Control-K>", lambda _e: self._focus_cek_ticker())
        self.root.bind("<Control-f>", lambda _e: self._focus_active_search())
        self.root.bind("<Control-F>", lambda _e: self._focus_active_search())
        for idx in range(1, 9):
            self.root.bind(f"<Control-Key-{idx}>", lambda _e, i=idx - 1: self.notebook.select(i))
        # Zoom ukuran font seluruh UI (Ctrl + / - / 0)
        self.root.bind("<Control-equal>", lambda _e: self._zoom(0.1))
        self.root.bind("<Control-plus>", lambda _e: self._zoom(0.1))
        self.root.bind("<Control-minus>", lambda _e: self._zoom(-0.1))
        self.root.bind("<Control-Key-0>", lambda _e: self._zoom(None))

    # ------------------------------------------------------------------
    # UI/UX: jam bursa, status bar hidup, zoom font, persistensi sesi
    # ------------------------------------------------------------------

    def _tick_clock(self) -> None:
        """Perbarui label jam WIB + status bursa setiap 1 detik."""
        if self._closing:
            return
        try:
            text, status = market_clock_text()
            self.lbl_clock.configure(
                text=text, fg=THEME["green"] if status == "open" else THEME["dim"],
            )
        except Exception:
            pass
        self._clock_after_id = self.root.after(1000, self._tick_clock)

    def _tick_statusbar(self) -> None:
        """Status bar hidup: spinner proses aktif, countdown auto-refresh, jam update."""
        if self._closing:
            return
        self._status_tick += 1
        parts: list[str] = []
        busy = sum(1 for v in self._busy_flags.values() if v)
        if busy:
            parts.append(f"{spinner_frame(self._status_tick)} {busy} proses berjalan")
        if self._next_auto_ts is not None:
            parts.append(f"auto-refresh {countdown_text(self._next_auto_ts - time.time())}")
        if self._last_update_ts:
            parts.append(f"update {self._last_update_ts}")
        try:
            self.lbl_status_extra.configure(text="   ".join(parts))
        except Exception:
            pass
        if self._busy_keys:
            # animasikan spinner pada tombol yang sedang sibuk
            self._refresh_busy_ui()
        self._status_after_id = self.root.after(500, self._tick_statusbar)

    def _zoom(self, delta: float | None) -> None:
        """Ubah skala font seluruh UI; ``delta=None`` untuk reset ke 1.0x."""
        cur = store_profile.clamp_font_scale(self._ui_state.get("font_scale", 1.0))
        new = 1.0 if delta is None else store_profile.clamp_font_scale(cur + delta)
        self._apply_font_scale(new, persist=True)

    def _apply_font_scale(self, scale: Any, *, persist: bool = True) -> None:
        s = store_profile.clamp_font_scale(scale)
        self._ui_state["font_scale"] = s
        try:
            self.root.tk.call("tk", "scaling", s)
        except Exception:
            pass
        if persist:
            self._save_ui_state()
            self.var_status.set(f"Skala font UI: {s:.2f}x (Ctrl+0 untuk reset).")

    def _save_ui_state(self, **overrides: Any) -> None:
        """Simpan state sesi UI (geometri, tab, auto-refresh, toast, zoom, riwayat) ke SQLite."""
        try:
            cid = self._current_chat_id()
            state = dict(self._ui_state)
            if hasattr(self, "var_refresh_interval"):
                state["refresh"] = self.var_refresh_interval.get()
            if hasattr(self, "var_toast"):
                state["toast"] = bool(self.var_toast.get())
            if self.root.wm_state() == "normal":
                state["geometry"] = self.root.wm_geometry()
            try:
                state["tab_index"] = self.notebook.index(self.notebook.select())
            except Exception:
                pass
            state["recent_tickers"] = list(self._recent_tickers)
            state.update(overrides)
            self._ui_state = store_profile.set_desktop_ui_state(cid, **state)
        except Exception:
            log.exception("Gagal menyimpan state UI desktop")

    def _update_tab_badges(self) -> None:
        """Badge counter pada tab sesuai jumlah data terakhir (Watchlist/Sinyal/Paket/Portofolio)."""
        try:
            self.notebook.tab(1, text=tab_label("⭐ Watchlist", len(self._last_watch_rows)))
            self.notebook.tab(2, text=tab_label("🎯 Sinyal Pasar", len(self._last_sinyal_rows)))
            plans = (self._last_paket_res or {}).get("plans") or []
            self.notebook.tab(3, text=tab_label("📦 Paket Beli", len(plans)))
            self.notebook.tab(4, text=tab_label("💼 Portofolio", len(self._last_port_rows)))
        except Exception:
            pass

    def _add_recent_ticker(self, sym: str) -> None:
        """Catat ticker yang berhasil dicek ke riwayat (combobox Cek Emiten)."""
        t = str(sym or "").strip().upper().removesuffix(".JK")
        if not t:
            return
        if t in self._recent_tickers:
            self._recent_tickers.remove(t)
        self._recent_tickers.insert(0, t)
        self._recent_tickers = self._recent_tickers[: store_profile.RECENT_TICKERS_MAX]
        try:
            self.ent_cek.configure(values=list(self._recent_tickers))
        except Exception:
            pass
        self._save_ui_state()

    # ------------------------------------------------------------------
    # Indikator loading per tombol: tombol menunjukkan prosesnya berjalan
    # ------------------------------------------------------------------

    def _reg_busy(self, key: str, *buttons: Any) -> None:
        """Daftarkan tombol yang menunjukkan estado sibuk saat worker ``key`` jalan."""
        self._busy_buttons.setdefault(key, []).extend(buttons)

    def _apply_btn_busy(self, btn: Any, busy: bool) -> None:
        if busy:
            if not hasattr(btn, "_orig_text"):
                btn._orig_text = str(btn.cget("text"))  # type: ignore[attr-defined]
            btn.state(["disabled"])
            btn.configure(text=busy_text(btn._orig_text, self._status_tick))  # type: ignore[attr-defined]
        else:
            if hasattr(btn, "_orig_text"):
                btn.configure(text=btn._orig_text)  # type: ignore[attr-defined]
                del btn._orig_text  # type: ignore[attr-defined]
            btn.state(["!disabled"])

    def _refresh_busy_ui(self) -> None:
        """Segarkan label semua tombol yang terdaftar + tombol global topbar."""
        for key, buttons in self._busy_buttons.items():
            busy = key in self._busy_keys
            for btn in buttons:
                try:
                    self._apply_btn_busy(btn, busy)
                except Exception:
                    pass
        any_busy = bool(self._busy_keys)
        for btn in self._global_busy_buttons:
            try:
                self._apply_btn_busy(btn, any_busy)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Teks penjelasan rapi: rich text (heading/warna/bold/italic) + spasi
    # ------------------------------------------------------------------

    def _rich_tags(self, widget: tk.Text) -> None:
        widget.tag_configure("heading", foreground=THEME["accent"], font=FONT_UI_BOLD)
        widget.tag_configure("good", foreground=THEME["green"])
        widget.tag_configure("bad", foreground=THEME["red"])
        widget.tag_configure("warn", foreground=THEME["amber"])
        widget.tag_configure("info", foreground=THEME["accent"])
        widget.tag_configure("muted", foreground=THEME["dim"])
        widget.tag_configure("bold", font=FONT_UI_BOLD)
        widget.tag_configure("italic", font=("Segoe UI", 10, "italic"))

    def _set_rich_text(self, widget: scrolledtext.ScrolledText, content: str) -> None:
        """Tampilkan teks penjelasan dengan hierarki visual (judul, warna verdict, bold)."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        self._rich_tags(widget)
        inline_tag = {"b": "bold", "i": "italic"}
        for line in str(content or "").split("\n"):
            base = classify_line(line)
            segs = inline_segments(line)
            if not segs:
                widget.insert("end", "\n", (base,))
                continue
            for text, inline in segs:
                tags = (base, inline_tag[inline]) if inline else (base,)
                widget.insert("end", text, tags)
            widget.insert("end", "\n", (base,))
        widget.configure(state="disabled")

    def _set_plan_cards(self, levels: dict[str, str]) -> None:
        """Tampilkan kartu ringkasan rencana beli (Entry/SL/TP/Lot/Biaya/Risiko) di tab Cek."""
        get = levels.get
        _set_kpi(self.pc_entry, get("entry", "-"), f"pullback {get('pullback', '-')}", color=THEME["accent"])
        _set_kpi(self.pc_sl, get("sl", "-"), "batas rugi jika harga turun ke SL", color=THEME["red"])
        _set_kpi(self.pc_tp, get("tp", "-"), "target ambil untung (TP)", color=THEME["green"])
        _set_kpi(self.pc_lots, get("lots", "-"), "1 lot = 100 saham", color=THEME["fg"])
        _set_kpi(self.pc_cost, get("cost", "-"), "entry × lot × 100", color=THEME["fg"])
        _set_kpi(self.pc_risk, get("risk", "-"), "kehilangan maks bila SL tersentuh", color=THEME["amber"])

    # ------------------------------------------------------------------
    # Semua fitur saling terhubung: lompat lintas tab & klik grafik sektor
    # ------------------------------------------------------------------

    _TAB_NAMES = (
        "Pasar & Rezim", "Watchlist", "Sinyal Pasar", "Paket Beli",
        "Portofolio", "Jurnal", "Cek Emiten", "Alert",
    )

    def _goto_tab(self, idx: int, refresh: Callable[[], None] | None = None) -> None:
        """Lompat ke tab ``idx`` (0..7) dan opsional menjalankan refresh-nya."""
        try:
            self.notebook.select(idx)
        except Exception:
            pass
        if refresh is not None:
            try:
                refresh()
            except Exception:
                log.exception("Refresh saat lompat tab gagal")
        name = self._TAB_NAMES[idx] if 0 <= idx < len(self._TAB_NAMES) else str(idx)
        self.var_status.set(f"Loncat ke tab {name}.")

    def _on_sector_motion(self, event: tk.Event) -> None:
        hit = hit_test_rects(self._sector_rects, event.x, event.y)
        try:
            self.canvas_sectors.configure(cursor="hand2" if hit else "")
        except Exception:
            pass

    def _on_sector_click(self, event: tk.Event) -> None:
        name = hit_test_rects(self._sector_rects, event.x, event.y)
        if not name:
            return
        options = [str(v) for v in (self.cb_sinyal_sector.cget("values") or [])]
        match = match_sector_option(name, options)
        if match and match != "Semua Sektor":
            self.var_sinyal_sector.set(match)
            self._goto_tab(2, self.refresh_sinyal)
            self.var_status.set(
                f"Sinyal Pasar difilter sektor '{match}' (dari grafik rotasi). "
                "Pilih 'Semua Sektor' untuk reset."
            )
        else:
            self.var_status.set(
                f"Sektor '{name}' belum ada di snapshot /scan — jalankan ⚡ Scan BEI lebih dulu."
            )

    def _cek_goto_sinyal(self) -> None:
        sym = self.var_cek_ticker.get().strip().upper().replace(".JK", "")
        if sym:
            self.var_sinyal_search.set(sym)
        self._goto_tab(2, self.refresh_sinyal)

    def _watch_goto_sinyal(self, sym: str | None) -> None:
        if sym:
            self.var_sinyal_search.set(str(sym).upper())
        self._goto_tab(2, self.refresh_sinyal)

    def _cek_goto_paket(self) -> None:
        self._goto_tab(3, self.refresh_paket)

    def _alerts_context_menu(self, event: tk.Event) -> None:
        iid = self.tree_alerts.identify_row(event.y)
        if not iid:
            return
        self.tree_alerts.selection_set(iid)
        sym = self._selected_ticker(self.tree_alerts, 2)
        items: list[tuple[str, Callable[[], None]]] = [
            ("🔍 Cek Emiten Lengkap", self._alert_open_cek),
        ]
        if sym:
            items.append(("⭐ Tambah ke Watchlist", lambda s=sym: self._add_watchlist(s)))
        self._show_popup(event, items)

    # ------------------------------------------------------------------
    # Tab 1: Pasar & Rezim + Rotasi Sektor
    # ------------------------------------------------------------------

    def _build_market_tab(self) -> None:
        ctrl = tk.Frame(self.tab_market, bg=THEME["bg"], padx=8, pady=8)
        ctrl.pack(fill="x")
        tk.Label(
            ctrl,
            text="Ringkasan Kondisi Pasar BEI, Rezim IHSG, Breadth /scan, Asing & Sentimen",
            bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL_BOLD,
        ).pack(side="left")
        # Lompat cepat ke fitur terkait (semua fitur saling terhubung)
        nav = tk.Frame(ctrl, bg=THEME["bg"])
        nav.pack(side="right", padx=(0, 8))
        for label, idx, fn in (
            ("⭐ Watchlist", 1, self.refresh_watch),
            ("🎯 Sinyal", 2, self.refresh_sinyal),
            ("📦 Paket", 3, self.refresh_paket),
            ("💼 Portofolio", 4, self.refresh_port),
        ):
            ttk.Button(
                nav, text=label, style="Ghost.TButton",
                command=lambda i=idx, f=fn: self._goto_tab(i, f),
            ).pack(side="left", padx=(0, 4))
        btn_market = ttk.Button(
            ctrl, text="↻ Segarkan Pasar & IDX", style="Accent.TButton",
            command=lambda: self.refresh_market(force=True),
        )
        btn_market.pack(side="right")
        self._reg_busy("market", btn_market)

        kpi_row = tk.Frame(self.tab_market, bg=THEME["bg"], padx=8)
        kpi_row.pack(fill="x", pady=(0, 8))
        for c in range(5):
            kpi_row.columnconfigure(c, weight=1, uniform="mkpi")

        self.mk_ihsg = _make_kpi_card(kpi_row, "IHSG (^JKSE)")
        self.mk_regime = _make_kpi_card(kpi_row, "Rezim & Skala")
        self.mk_breadth = _make_kpi_card(kpi_row, "Breadth /scan")
        self.mk_foreign = _make_kpi_card(kpi_row, "Aliran Asing BEI")
        self.mk_cond = _make_kpi_card(kpi_row, "Kondisi & Kesegaran")
        for idx, card in enumerate(
            (self.mk_ihsg, self.mk_regime, self.mk_breadth, self.mk_foreign, self.mk_cond)
        ):
            card["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        body = tk.PanedWindow(
            self.tab_market, orient="horizontal", bg=THEME["bg"], sashwidth=5, bd=0,
        )
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=10, pady=8,
        )
        tk.Label(
            left, text="ROTASI SEKTOR BEI (IDX-IC 1 HARI) — klik bar untuk filter Sinyal Pasar",
            bg=THEME["card"], fg=THEME["accent"], font=FONT_SMALL_BOLD, anchor="w",
        ).pack(fill="x", pady=(0, 6))
        self.canvas_sectors = tk.Canvas(
            left, bg=THEME["bg_elev"], highlightthickness=0, bd=0,
        )
        self.canvas_sectors.pack(fill="both", expand=True)
        self.canvas_sectors.bind("<Configure>", lambda _e: self._draw_sector_bars())
        # Klik bar sektor -> lompat ke Sinyal Pasar dengan filter sektor tersebut
        self.canvas_sectors.bind("<Button-1>", self._on_sector_click)
        self.canvas_sectors.bind("<Motion>", self._on_sector_motion)
        body.add(left, stretch="always", minsize=420)

        right = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=10, pady=8,
        )
        tk.Label(
            right, text="DIAGNOSTIK PASAR & SENTIMEN BERITA", bg=THEME["card"],
            fg=THEME["accent"], font=FONT_SMALL_BOLD, anchor="w",
        ).pack(fill="x", pady=(0, 6))
        self.txt_market = scrolledtext.ScrolledText(
            right,
            wrap="word",
            bg=THEME["bg_elev"],
            fg=THEME["fg"],
            insertbackground=THEME["accent"],
            font=FONT_UI,
            relief="flat",
            padx=10,
            pady=8,
            spacing1=2,
            spacing3=5,
        )
        self.txt_market.pack(fill="both", expand=True)
        self.txt_market.configure(state="disabled")
        body.add(right, stretch="always", minsize=400)

    # ------------------------------------------------------------------
    # Tab 2: Watchlist + Quick Alert Editor + CSV
    # ------------------------------------------------------------------

    def _build_watch_tab(self) -> None:
        bar = tk.Frame(self.tab_watch, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")

        tk.Label(bar, text="Ticker:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        self.ent_watch_add = self._entry(bar, self.var_watch_add, width=10)
        self.ent_watch_add.pack(side="left", padx=(0, 4))
        self.ent_watch_add.bind("<Return>", lambda _e: self._add_watchlist())
        ttk.Button(bar, text="+ Tambah", style="Accent.TButton", command=self._add_watchlist).pack(
            side="left", padx=(0, 10),
        )

        tk.Label(bar, text="Cari:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        self.ent_watch_search = self._entry(bar, self.var_watch_search, width=14)
        self.ent_watch_search.pack(side="left", padx=(0, 10))
        self.var_watch_search.trace_add("write", lambda *_: self._render_watch_table())

        ttk.Button(
            bar, text="Cek Emiten", style="Ghost.TButton", command=self._watch_open_cek,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            bar, text="🔔 Atur Alert", style="Ghost.TButton", command=self._watch_edit_alerts,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            bar, text="Catat Beli", style="Ghost.TButton", command=self._watch_prefill_buy,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            bar, text="Hapus", style="Danger.TButton", command=self._remove_watchlist,
        ).pack(side="left", padx=(0, 4))

        ttk.Button(
            bar, text="Ekspor CSV", style="Ghost.TButton", command=self._export_watch_csv,
        ).pack(side="right")

        kpi = tk.Frame(self.tab_watch, bg=THEME["bg"], padx=8)
        kpi.pack(fill="x", pady=(0, 8))
        for c in range(4):
            kpi.columnconfigure(c, weight=1, uniform="wkpi")
        self.wk_total = _make_kpi_card(kpi, "Total Watchlist")
        self.wk_beli = _make_kpi_card(kpi, "Sinyal BELI")
        self.wk_zona = _make_kpi_card(kpi, "Zona Beli Aktif")
        self.wk_alert = _make_kpi_card(kpi, "Alert Terpicu / Stale")
        for idx, c in enumerate((self.wk_total, self.wk_beli, self.wk_zona, self.wk_alert)):
            c["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        self.tree_watch = _build_treeview(
            self.tab_watch,
            [
                ("ticker", "Ticker", 80, "center", "str"),
                ("sector", "Sektor", 130, "w", "str"),
                ("syariah", "ISSI", 58, "center", "str"),
                ("price", "Harga", 88, "e", "num"),
                ("ret1d", "1H %", 86, "e", "num"),
                ("score", "Skor", 70, "e", "num"),
                ("signal", "Sinyal", 76, "center", "str"),
                ("entry", "Entry", 84, "e", "num"),
                ("sl", "SL", 80, "e", "num"),
                ("tp", "TP", 80, "e", "num"),
                ("alerts_cfg", "Alert Aktif", 135, "w", "str"),
                ("reasons", "Zona / Trigger / Catatan", 280, "w", "str"),
            ],
            on_double_click=lambda _e: self._watch_open_cek(),
            on_right_click=self._watch_context_menu,
        )
        self.tree_watch.bind("<Delete>", lambda _e: self._remove_watchlist())

    # ------------------------------------------------------------------
    # Tab 3: Sinyal Pasar (/scan lokal)
    # ------------------------------------------------------------------

    def _build_sinyal_tab(self) -> None:
        bar = tk.Frame(self.tab_sinyal, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")

        for label, val in (("Semua", "ALL"), ("BELI", "BELI"), ("TAHAN", "TAHAN"), ("JUAL", "JUAL")):
            tk.Radiobutton(
                bar,
                text=label,
                variable=self.var_sinyal_filter,
                value=val,
                command=self.refresh_sinyal,
                bg=THEME["card"],
                fg=THEME["fg"],
                selectcolor=THEME["accent_dark"],
                activebackground=THEME["card_hover"],
                activeforeground=THEME["fg"],
                indicatoron=False,
                padx=10,
                pady=4,
                font=FONT_SMALL_BOLD,
                relief="flat",
                bd=0,
            ).pack(side="left", padx=(0, 4))

        tk.Label(bar, text="Cari:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(10, 4),
        )
        self.ent_sinyal_search = self._entry(bar, self.var_sinyal_search, width=14)
        self.ent_sinyal_search.pack(side="left", padx=(0, 8))
        self.ent_sinyal_search.bind("<Return>", lambda _e: self.refresh_sinyal())

        tk.Label(bar, text="Sektor:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        self.cb_sinyal_sector = ttk.Combobox(
            bar,
            textvariable=self.var_sinyal_sector,
            values=["Semua Sektor"],
            width=18,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.cb_sinyal_sector.pack(side="left", padx=(0, 8))
        self.cb_sinyal_sector.bind("<<ComboboxSelected>>", lambda _e: self.refresh_sinyal())

        self.sw_quality = Switch(
            bar,
            text="Saring Kualitas",
            variable=self.var_sinyal_quality,
            command=self.refresh_sinyal,
            bg=THEME["bg"],
        )
        self.sw_quality.pack(side="left", padx=(4, 8))

        btn_terapkan = ttk.Button(
            bar, text="Terapkan", style="Accent.TButton", command=self.refresh_sinyal,
        )
        btn_terapkan.pack(side="left", padx=(0, 4))
        btn_scan = ttk.Button(
            bar, text="⚡ Scan BEI (~944)", style="Ghost.TButton", command=self._run_full_scan,
        )
        btn_scan.pack(side="left", padx=(0, 4))
        self._reg_busy("sinyal", btn_terapkan)
        self._reg_busy("scan", btn_scan)
        ttk.Button(
            bar, text="Ekspor CSV", style="Ghost.TButton", command=self._export_sinyal_csv,
        ).pack(side="right")

        kpi = tk.Frame(self.tab_sinyal, bg=THEME["bg"], padx=8)
        kpi.pack(fill="x", pady=(0, 8))
        for c in range(5):
            kpi.columnconfigure(c, weight=1, uniform="skpi")
        self.sk_scan_date = _make_kpi_card(kpi, "Snapshot /scan")
        self.sk_matched = _make_kpi_card(kpi, "Hasil Filter")
        self.sk_beli = _make_kpi_card(kpi, "Total BELI")
        self.sk_tahan = _make_kpi_card(kpi, "Total TAHAN")
        self.sk_jual = _make_kpi_card(kpi, "Total JUAL")
        for idx, c in enumerate(
            (self.sk_scan_date, self.sk_matched, self.sk_beli, self.sk_tahan, self.sk_jual)
        ):
            c["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        self.tree_sinyal = _build_treeview(
            self.tab_sinyal,
            [
                ("ticker", "Ticker", 80, "center", "str"),
                ("name", "Emiten", 150, "w", "str"),
                ("sector", "Sektor", 130, "w", "str"),
                ("syariah", "ISSI", 58, "center", "str"),
                ("price", "Harga", 88, "e", "num"),
                ("ret1d", "1H %", 86, "e", "num"),
                ("score", "Skor", 70, "e", "num"),
                ("signal", "Sinyal", 76, "center", "str"),
                ("rsi", "RSI14", 66, "e", "num"),
                ("vol_ratio", "Vol/Avg", 74, "e", "num"),
                ("reasons", "Alasan / Kualitas Fundamental", 300, "w", "str"),
            ],
            on_double_click=lambda _e: self._sinyal_open_cek(),
            on_right_click=self._sinyal_context_menu,
        )

    # ------------------------------------------------------------------
    # Tab 4: Paket Rekomendasi (/paket)
    # ------------------------------------------------------------------

    def _build_paket_tab(self) -> None:
        bar = tk.Frame(self.tab_paket, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")

        tk.Label(bar, text="Maks Emiten:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        cb_max = ttk.Combobox(
            bar, textvariable=self.var_paket_max, values=["1", "2", "3", "4", "5"],
            width=4, state="readonly", style="Dark.TCombobox",
        )
        cb_max.pack(side="left", padx=(0, 8))
        cb_max.bind("<<ComboboxSelected>>", lambda _e: self.refresh_paket())

        btn_paket = ttk.Button(
            bar, text="↻ Hitung Ulang Paket", style="Accent.TButton", command=self.refresh_paket,
        )
        btn_paket.pack(side="left", padx=(0, 6))
        self._reg_busy("paket", btn_paket)
        ttk.Button(
            bar, text="📋 Salin Order Stockbit", style="Ghost.TButton", command=self._copy_paket_orders,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Catat Beli Terpilih", style="Ghost.TButton", command=self._paket_prefill_buy,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Ekspor CSV", style="Ghost.TButton", command=self._export_paket_csv,
        ).pack(side="right")

        kpi = tk.Frame(self.tab_paket, bg=THEME["bg"], padx=8)
        kpi.pack(fill="x", pady=(0, 8))
        for c in range(4):
            kpi.columnconfigure(c, weight=1, uniform="pkpi")
        self.pk_modal = _make_kpi_card(kpi, "Modal & Skala Rezim")
        self.pk_alloc = _make_kpi_card(kpi, "Total Alokasi Paket")
        self.pk_cash = _make_kpi_card(kpi, "Sisa Kas")
        self.pk_risk = _make_kpi_card(kpi, "Total Risiko ke SL")
        for idx, c in enumerate((self.pk_modal, self.pk_alloc, self.pk_cash, self.pk_risk)):
            c["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        self.tree_paket = _build_treeview(
            self.tab_paket,
            [
                ("grade", "Grade", 62, "center", "str"),
                ("ticker", "Ticker", 78, "center", "str"),
                ("sector", "Sektor", 120, "w", "str"),
                ("syariah", "ISSI", 55, "center", "str"),
                ("entry", "Entry", 82, "e", "num"),
                ("pullback", "Pullback", 82, "e", "num"),
                ("sl", "SL", 78, "e", "num"),
                ("tp", "TP", 78, "e", "num"),
                ("lots", "Lot", 56, "e", "num"),
                ("buy_cost", "Est. Biaya", 112, "e", "num"),
                ("real_risk", "Risiko SL", 104, "e", "num"),
                ("warnings", "Catatan / Risiko", 280, "w", "str"),
            ],
            height=10,
            on_double_click=lambda _e: self._paket_open_cek(),
            on_right_click=self._paket_context_menu,
        )

        self.lbl_paket_footer = tk.Label(
            self.tab_paket,
            text="",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=FONT_SMALL,
            anchor="w",
            padx=10,
            pady=6,
            justify="left",
        )
        self.lbl_paket_footer.pack(fill="x", padx=8, pady=(0, 8))

    # ------------------------------------------------------------------
    # Tab 5: Portofolio Asli + Risiko VaR + Pelacak Tesis
    # ------------------------------------------------------------------

    def _build_port_tab(self) -> None:
        kpi = tk.Frame(self.tab_port, bg=THEME["bg"], padx=8, pady=8)
        kpi.pack(fill="x")
        for c in range(4):
            kpi.columnconfigure(c, weight=1, uniform="prkpi")
        self.pt_val = _make_kpi_card(kpi, "Nilai Pasar / Cost")
        self.pt_pnl = _make_kpi_card(kpi, "PnL Belum Realisasi")
        self.pt_sl = _make_kpi_card(kpi, "Risiko ke Stop-Loss")
        self.pt_var = _make_kpi_card(kpi, "VaR 95% / Batas Risiko")
        for idx, c in enumerate((self.pt_val, self.pt_pnl, self.pt_sl, self.pt_var)):
            c["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        self.lbl_port_warn = tk.Label(
            self.tab_port,
            text="",
            bg=THEME["amber_soft"],
            fg=THEME["amber"],
            font=FONT_SMALL_BOLD,
            anchor="w",
            padx=10,
            pady=4,
        )

        form = tk.Frame(
            self.tab_port,
            bg=THEME["card"],
            highlightbackground=THEME["border"],
            highlightthickness=1,
            padx=10,
            pady=7,
        )
        form.pack(fill="x", padx=8, pady=(0, 8))

        tk.Label(form, text="Catat Eksekusi Manual Stockbit:", bg=THEME["card"],
                 fg=THEME["accent"], font=FONT_SMALL_BOLD).pack(side="left", padx=(0, 10))
        tk.Label(form, text="Kode:", bg=THEME["card"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 3),
        )
        ent_pt = self._entry(form, self.var_port_ticker, width=8)
        ent_pt.pack(side="left", padx=(0, 6))
        ttk.Combobox(
            form, textvariable=self.var_port_side, values=["BELI", "JUAL"],
            width=6, state="readonly", style="Dark.TCombobox",
        ).pack(side="left", padx=(0, 6))
        tk.Label(form, text="Harga:", bg=THEME["card"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 3),
        )
        ent_pp = self._entry(form, self.var_port_price, width=9)
        ent_pp.pack(side="left", padx=(0, 6))
        tk.Label(form, text="Lot:", bg=THEME["card"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 3),
        )
        ent_pl = self._entry(form, self.var_port_lots, width=6)
        ent_pl.pack(side="left", padx=(0, 6))
        tk.Label(form, text="Catatan:", bg=THEME["card"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 3),
        )
        ent_pn = self._entry(form, self.var_port_notes, width=16)
        ent_pn.pack(side="left", padx=(0, 8))
        for ent in (ent_pt, ent_pp, ent_pl, ent_pn):
            ent.bind("<Return>", lambda _e: self._submit_execution())

        ttk.Button(
            form, text="Simpan Eksekusi", style="Accent.TButton", command=self._submit_execution,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            form, text="Siapkan Jual", style="Ghost.TButton", command=self._port_prefill_sell,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            form, text="⚙ Batas Risiko", style="Ghost.TButton", command=self._open_risk_limit_dialog,
        ).pack(side="right", padx=(4, 0))
        ttk.Button(
            form, text="Ekspor CSV", style="Ghost.TButton", command=self._export_port_csv,
        ).pack(side="right")

        self.tree_port = _build_treeview(
            self.tab_port,
            [
                ("ticker", "Ticker", 76, "center", "str"),
                ("sector", "Sektor", 112, "w", "str"),
                ("lots", "Lot", 54, "e", "num"),
                ("avg", "Avg Beli", 80, "e", "num"),
                ("price", "Harga", 80, "e", "num"),
                ("ret1d", "1H %", 78, "e", "num"),
                ("value", "Nilai Pasar", 112, "e", "num"),
                ("pnl_rp", "PnL Rp", 104, "e", "num"),
                ("pnl_pct", "PnL %", 78, "e", "num"),
                ("sl", "SL Efektif", 112, "e", "num"),
                ("tp", "TP1 / TP", 104, "e", "str"),
                ("days", "Hari", 52, "e", "num"),
                ("thesis", "Tesis", 92, "center", "str"),
                ("guidance", "Panduan Exit & Peringatan", 250, "w", "str"),
            ],
            on_double_click=lambda _e: self._port_open_cek(),
            on_right_click=self._port_context_menu,
        )

    # ------------------------------------------------------------------
    # Tab 6: Jurnal & Evaluasi (MAE/MFE + Alpha vs IHSG)
    # ------------------------------------------------------------------

    def _build_jurnal_tab(self) -> None:
        bar = tk.Frame(self.tab_jurnal, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text="Riwayat Terakhir:", bg=THEME["bg"], fg=THEME["muted"],
                 font=FONT_SMALL).pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(
            bar, textvariable=self.var_jurnal_limit, values=["25", "50", "100", "200"],
            width=6, state="readonly", style="Dark.TCombobox",
        )
        cb.pack(side="left", padx=(0, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_jurnal())
        btn_jurnal = ttk.Button(
            bar, text="↻ Segarkan Jurnal", style="Accent.TButton", command=self.refresh_jurnal,
        )
        btn_jurnal.pack(side="left")
        self._reg_busy("jurnal", btn_jurnal)
        ttk.Button(
            bar, text="Ekspor Eksekusi CSV", style="Ghost.TButton", command=self._export_jurnal_csv,
        ).pack(side="right")

        kpi = tk.Frame(self.tab_jurnal, bg=THEME["bg"], padx=8)
        kpi.pack(fill="x", pady=(0, 8))
        for c in range(5):
            kpi.columnconfigure(c, weight=1, uniform="jkpi")
        self.jk_execs = _make_kpi_card(kpi, "Eksekusi Asli")
        self.jk_wr = _make_kpi_card(kpi, "Win Rate / Closed")
        self.jk_pf = _make_kpi_card(kpi, "Profit Factor")
        self.jk_pnl = _make_kpi_card(kpi, "Realisasi PnL Bersih")
        self.jk_alpha = _make_kpi_card(kpi, "Rata R / Alpha vs IHSG")
        for idx, c in enumerate((self.jk_execs, self.jk_wr, self.jk_pf, self.jk_pnl, self.jk_alpha)):
            c["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        self.txt_jurnal = scrolledtext.ScrolledText(
            self.tab_jurnal,
            wrap="word",
            bg=THEME["bg_elev"],
            fg=THEME["fg"],
            insertbackground=THEME["accent"],
            font=FONT_MONO,
            relief="flat",
            padx=12,
            pady=10,
            spacing1=2,
            spacing3=5,
        )
        self.txt_jurnal.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.txt_jurnal.configure(state="disabled")

    # ------------------------------------------------------------------
    # Tab 7: Cek Emiten (Chart PIL + Rencana + Forensik + Fundamental + Berita)
    # ------------------------------------------------------------------

    def _build_cek_tab(self) -> None:
        bar = tk.Frame(self.tab_cek, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")

        tk.Label(bar, text="Ticker:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        # Combobox editable: riwayat ticker yang pernah dicek + input bebas
        self.ent_cek = ttk.Combobox(
            bar,
            textvariable=self.var_cek_ticker,
            values=list(self._recent_tickers),
            width=10,
            style="Dark.TCombobox",
        )
        self.ent_cek.pack(side="left", padx=(0, 6))
        self.ent_cek.bind("<Return>", lambda _e: self.refresh_cek())

        tk.Label(bar, text="Periode:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        cb_p = ttk.Combobox(
            bar, textvariable=self.var_cek_period, values=list(dm.VALID_CHART_PERIODS),
            width=6, state="readonly", style="Dark.TCombobox",
        )
        cb_p.pack(side="left", padx=(0, 8))
        cb_p.bind("<<ComboboxSelected>>", lambda _e: self.refresh_cek())

        btn_cek = ttk.Button(
            bar, text="🔍 Cek Lengkap", style="Accent.TButton", command=self.refresh_cek,
        )
        btn_cek.pack(side="left", padx=(0, 6))
        self._reg_busy("cek", btn_cek)
        ttk.Button(
            bar, text="+ Watchlist", style="Ghost.TButton", command=self._cek_add_watchlist,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="📋 Salin Order Stockbit", style="Ghost.TButton", command=self._copy_cek_order,
        ).pack(side="left", padx=(0, 6))
        # Lompat ke fitur terkait ticker yang sedang dicek
        ttk.Button(
            bar, text="→ Sinyal", style="Ghost.TButton", command=self._cek_goto_sinyal,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="→ Paket", style="Ghost.TButton", command=self._cek_goto_paket,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="💾 Simpan Chart PNG", style="Ghost.TButton", command=self._save_cek_chart_png,
        ).pack(side="right")

        self.frm_cek_chips = tk.Frame(self.tab_cek, bg=THEME["bg"], padx=8)
        self.frm_cek_chips.pack(fill="x", pady=(0, 4))
        self._rebuild_quick_chips()

        self.lbl_cek_header = tk.Label(
            self.tab_cek,
            text=(
                "Masukkan kode emiten (contoh: BBCA, TLKM, BBRI) lalu tekan Cek Lengkap — "
                "ringkasan Entry/SL/TP muncul di kartu di bawah. Daftar rekomendasi beli: tab 📦 Paket Beli."
            ),
            bg=THEME["card"],
            fg=THEME["fg"],
            font=FONT_UI_BOLD,
            anchor="w",
            padx=12,
            pady=8,
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.lbl_cek_header.pack(fill="x", padx=8, pady=(0, 6))

        # Kartu ringkasan RENCANA BELI (Entry/SL/TP/Lot/Biaya/Risiko) langsung terlihat
        plan_row = tk.Frame(self.tab_cek, bg=THEME["bg"], padx=8)
        plan_row.pack(fill="x", pady=(0, 6))
        for c in range(6):
            plan_row.columnconfigure(c, weight=1, uniform="plancard")
        self.pc_entry = _make_kpi_card(plan_row, "🎯 Entry")
        self.pc_sl = _make_kpi_card(plan_row, "🛡️ Stop-Loss")
        self.pc_tp = _make_kpi_card(plan_row, "🏁 Target TP")
        self.pc_lots = _make_kpi_card(plan_row, "Lot")
        self.pc_cost = _make_kpi_card(plan_row, "Est. Biaya")
        self.pc_risk = _make_kpi_card(plan_row, "Risiko ke SL")
        for idx, card in enumerate(
            (self.pc_entry, self.pc_sl, self.pc_tp, self.pc_lots, self.pc_cost, self.pc_risk)
        ):
            card["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        body = tk.PanedWindow(
            self.tab_cek, orient="horizontal", bg=THEME["bg"], sashwidth=6, bd=0,
        )
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=6, pady=6,
        )
        self.lbl_chart = tk.Label(
            left,
            text="Grafik 4-panel (Harga/MA/BB/SL-TP, Volume, RSI, MACD) akan tampil di sini.",
            bg=THEME["bg_elev"],
            fg=THEME["muted"],
            font=FONT_SMALL,
            compound="center",
        )
        self.lbl_chart.pack(fill="both", expand=True)
        self.lbl_chart.bind("<Configure>", lambda _e: self._resize_chart_preview())
        body.add(left, stretch="always", minsize=460)

        right = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=6, pady=6,
        )
        self.txt_cek = scrolledtext.ScrolledText(
            right,
            wrap="word",
            bg=THEME["bg_elev"],
            fg=THEME["fg"],
            insertbackground=THEME["accent"],
            font=FONT_UI,
            relief="flat",
            padx=10,
            pady=8,
            spacing1=2,
            spacing3=5,
        )
        self.txt_cek.pack(fill="both", expand=True)
        self.txt_cek.configure(state="disabled")
        body.add(right, stretch="always", minsize=400)

    # ------------------------------------------------------------------
    # Tab 8: Alert & Riwayat + Filter + Uji Toast
    # ------------------------------------------------------------------

    def _build_alerts_tab(self) -> None:
        bar = tk.Frame(self.tab_alerts, bg=THEME["bg"], padx=8, pady=8)
        bar.pack(fill="x")

        tk.Label(bar, text="Filter:", bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL).pack(
            side="left", padx=(0, 4),
        )
        cb = ttk.Combobox(
            bar,
            textvariable=self.var_alert_filter,
            values=["Semua", "Zona Beli", "Sinyal / Alert", "Exit / Tesis"],
            width=16,
            state="readonly",
            style="Dark.TCombobox",
        )
        cb.pack(side="left", padx=(0, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._render_alerts_table())

        ttk.Button(
            bar, text="Uji Toast Windows", style="Ghost.TButton",
            command=lambda: send_windows_toast("SahamBot Desktop", "Notifikasi desktop aktif & siap."),
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            bar, text="Salin Ringkasan", style="Ghost.TButton", command=self._copy_alerts_text,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            bar, text="Bersihkan Riwayat", style="Danger.TButton", command=self._clear_alerts,
        ).pack(side="right")

        self.tree_alerts = _build_treeview(
            self.tab_alerts,
            [
                ("time", "Waktu WIB", 130, "center", "str"),
                ("kind", "Kategori", 130, "center", "str"),
                ("ticker", "Ticker", 80, "center", "str"),
                ("message", "Detail Kondisi / Trigger", 620, "w", "str"),
            ],
            on_double_click=lambda _e: self._alert_open_cek(),
            on_right_click=self._alerts_context_menu,
        )

    # ------------------------------------------------------------------
    # Worker & Antrean UI Thread-Safe
    # ------------------------------------------------------------------

    def _run_async(self, key: str, fn: Callable[[], Any], done_event: str) -> None:
        if self._busy_flags.get(key):
            self._pending_jobs[key] = (fn, done_event)
            return
        self._busy_flags[key] = True
        self._busy_keys.add(key)
        self._refresh_busy_ui()

        def _worker() -> None:
            try:
                res = fn()
                self._ui_queue.put((done_event, (key, True, res)))
            except Exception as exc:
                log.exception("Worker desktop %s gagal", key)
                self._ui_queue.put((done_event, (key, False, str(exc))))

        threading.Thread(target=_worker, name=f"desktop-{key}", daemon=True).start()

    def _pump_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                ev, payload = self._ui_queue.get_nowait()
                if ev == "_tray_show":
                    self._restore_window()
                    continue
                if ev == "_tray_refresh":
                    self.refresh_all()
                    continue
                if ev == "_tray_quit":
                    self._quit_app()
                    return

                key, ok, data = payload
                self._busy_flags[key] = False
                self._busy_keys.discard(key)
                self._refresh_busy_ui()
                pending = self._pending_jobs.pop(key, None)
                if not ok:
                    self.var_status.set(f"[{key}] Gagal memuat: {data}")
                    if key == "scan":
                        messagebox.showwarning("Scan BEI", f"Scan gagal: {data}", parent=self.root)
                else:
                    self._last_update_ts = datetime.now().strftime("%H:%M:%S")
                    handler = {
                        "market": self._apply_market,
                        "watch": self._apply_watch,
                        "sinyal": self._apply_sinyal,
                        "scan": self._apply_scan_done,
                        "paket": self._apply_paket,
                        "port": self._apply_port,
                        "jurnal": self._apply_jurnal,
                        "cek": self._apply_cek,
                    }.get(ev)
                    if handler:
                        try:
                            handler(data)
                        except Exception as exc:
                            log.exception("Render UI desktop %s gagal", ev)
                            self.var_status.set(f"[{ev}] Gagal render UI: {exc}")
                if pending is not None and not self._closing:
                    p_fn, p_ev = pending
                    self._run_async(key, p_fn, p_ev)
        except queue.Empty:
            pass
        if not self._closing:
            self._pump_after_id = self.root.after(120, self._pump_queue)

    def _schedule_auto_refresh(self) -> None:
        mins = VALID_REFRESH_MIN.get(self.var_refresh_interval.get(), 0)
        if hasattr(self, "_auto_after_id") and self._auto_after_id:
            try:
                self.root.after_cancel(self._auto_after_id)
            except Exception:
                pass
            self._auto_after_id = None
        if mins > 0 and not self._closing:
            self._auto_after_id = self.root.after(mins * 60_000, self._auto_tick)
            self._next_auto_ts = time.time() + mins * 60
        else:
            self._next_auto_ts = None

    def _auto_tick(self) -> None:
        self._auto_after_id = None
        if self._closing:
            return
        if marketcal.is_market_open() or not self._last_market_pulse:
            self.refresh_all()
        else:
            self.refresh_market()
            self.refresh_watch()
            self.refresh_port()
        self._schedule_auto_refresh()

    # ------------------------------------------------------------------
    # Refresh Triggers
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.var_status.set("Menyegarkan data pasar, watchlist, sinyal, paket, portofolio & jurnal...")
        self.refresh_market()
        self.refresh_watch()
        self.refresh_sinyal()
        self.refresh_paket()
        self.refresh_port()
        self.refresh_jurnal()

    def refresh_active_tab(self) -> None:
        idx = self.notebook.index(self.notebook.select())
        actions = {
            0: self.refresh_market,
            1: self.refresh_watch,
            2: self.refresh_sinyal,
            3: self.refresh_paket,
            4: self.refresh_port,
            5: self.refresh_jurnal,
            6: self.refresh_cek,
            7: self._render_alerts_table,
        }
        fn = actions.get(idx, self.refresh_all)
        fn()

    def _on_tab_changed(self, _event: tk.Event) -> None:
        idx = self.notebook.index(self.notebook.select())
        self._save_ui_state(tab_index=idx)
        if idx == 6:
            if not self._last_cek_detail:
                self.refresh_cek()
            else:
                self.root.after(40, self._resize_chart_preview)

    def _current_chat_id(self) -> str:
        raw = self.var_profile.get().strip() if hasattr(self, "var_profile") else ""
        return raw or str(DESKTOP_CHAT_ID)

    def _get_modal_rp(self) -> float:
        raw = self.var_modal.get().strip()
        parsed = textutil.parse_rupiah(raw)
        if parsed is None:
            parsed = _safe_float(raw.replace(".", "").replace(",", ""), float(DEFAULT_MODAL_RP))
        return max(100_000.0, float(parsed or DEFAULT_MODAL_RP))

    def _save_modal(self) -> None:
        self._save_profile()

    def _save_profile(self) -> None:
        cid = self._current_chat_id()
        prev_prof = getattr(self, "_active_profile", cid)
        if cid != prev_prof:
            # Muat pengaturan persisten milik profil baru bila user ganti Profil/Chat ID
            self._active_profile = cid
            saved_modal = store_watchlist.get_modal(cid) or DEFAULT_MODAL_RP
            saved_syariah = bool(store_profile.get_syariah_mode(cid))
            self.var_modal.set(str(int(saved_modal)))
            self.var_syariah.set(saved_syariah)
            self.var_status.set(f"Profil beralih ke '{cid}' (Modal {_fmt_rp(saved_modal)})")
            self.refresh_all()
            return
        val = self._get_modal_rp()
        store_watchlist.set_modal(cid, int(round(val)))
        self.var_modal.set(str(int(round(val))))
        self._active_profile = cid
        self.var_status.set(f"Profil '{cid}' & modal {_fmt_rp(val)} disimpan.")
        self.refresh_watch()
        self.refresh_paket()
        self.refresh_port()

    def _on_toggle_syariah(self) -> None:
        cid = self._current_chat_id()
        enabled = bool(self.var_syariah.get())
        store_profile.set_syariah_mode(cid, enabled)
        label = "AKTIF (hanya saham ISSI)" if enabled else "NONAKTIF (semua saham BEI)"
        self.var_status.set(f"Mode Syariah (ISSI): {label}")
        self.refresh_watch()
        self.refresh_sinyal()
        self.refresh_paket()

    def _regime_ctx(self) -> dict[str, Any] | None:
        ihsg = (self._last_market_pulse or {}).get("ihsg") or {}
        reg = ihsg.get("regime")
        return reg if isinstance(reg, dict) else None

    def refresh_market(self, force: bool = True) -> None:
        if not force and self._last_market_pulse:
            return
        self.var_status.set("Mengambil data IHSG, breadth /scan, sektor BEI & berita pasar...")
        self._run_async(
            "market",
            lambda: dm.fetch_market_pulse(include_idx=True, include_news=True),
            "market",
        )

    def refresh_watch(self) -> None:
        cid = self._current_chat_id()
        tickers = store_watchlist.get_watchlist(cid)
        alerts_map = store_watchlist.get_alerts(cid)
        modal = self._get_modal_rp()
        syariah_only = bool(self.var_syariah.get())
        reg = self._regime_ctx()
        self._run_async(
            "watch",
            lambda: dm.evaluate_watchlist_rows(
                tickers,
                modal,
                market_regime=reg,
                syariah_only=syariah_only,
                alerts_map=alerts_map,
            ),
            "watch",
        )

    def refresh_sinyal(self) -> None:
        sig = self.var_sinyal_filter.get()
        qual = bool(self.var_sinyal_quality.get())
        syar = bool(self.var_syariah.get())
        q = self.var_sinyal_search.get().strip()
        sec = self.var_sinyal_sector.get()
        sector_filter = "" if sec == "Semua Sektor" else sec
        self._run_async(
            "sinyal",
            lambda: dm.load_sinyal_rows(
                sig,
                quality_only=qual,
                syariah_only=syar,
                search=q,
                sector=sector_filter,
                limit=200,
            ),
            "sinyal",
        )

    def _run_full_scan(self) -> None:
        if self._busy_flags.get("scan"):
            messagebox.showinfo("Scan BEI", "Scan penuh ~944 emiten sedang berjalan di latar belakang.", parent=self.root)
            return
        self.var_status.set("Menjalankan scan pasar BEI (~944 emiten, ±2-5 menit)...")
        self._run_async("scan", lambda: dm.run_full_market_scan(force=True), "scan")

    def _apply_scan_done(self, res: dict[str, Any]) -> None:
        cnt = res.get("count") or res.get("scanned") or 0
        self.var_status.set(f"Scan BEI selesai ({cnt} emiten diproses). Memperbarui Sinyal, Paket & Pasar...")
        if self.var_toast.get():
            send_windows_toast("Scan BEI Selesai", "Snapshot sinyal & breadth pasar telah diperbarui.")
        self.refresh_sinyal()
        self.refresh_paket()
        self.refresh_market()

    def refresh_paket(self) -> None:
        cid = self._current_chat_id()
        modal = self._get_modal_rp()
        try:
            max_p = max(1, min(5, int(self.var_paket_max.get())))
        except ValueError:
            max_p = 5
        syar = bool(self.var_syariah.get())
        reg = self._regime_ctx()
        self._run_async(
            "paket",
            lambda: dm.build_paket_picks(
                modal,
                chat_id=cid,
                max_plans=max_p,
                market_regime=reg,
                syariah_only=syar,
            ),
            "paket",
        )

    def refresh_port(self) -> None:
        cid = self._current_chat_id()
        reg = self._regime_ctx()

        def _work() -> dict[str, Any]:
            rows = dm.evaluate_portfolio_rows(cid, market_regime=reg)
            summary = dm.compute_portfolio_risk_summary(cid, rows)
            return {"rows": rows, "summary": summary}

        self._run_async("port", _work, "port")

    def refresh_jurnal(self) -> None:
        cid = self._current_chat_id()
        try:
            lim = max(10, min(200, int(self.var_jurnal_limit.get())))
        except ValueError:
            lim = 50
        self._run_async("jurnal", lambda: dm.build_journal_summary(cid, limit=lim), "jurnal")

    def refresh_cek(self) -> None:
        sym = self.var_cek_ticker.get().strip().upper().replace(".JK", "")
        if not sym:
            messagebox.showinfo("Cek Emiten", "Masukkan kode ticker BEI (contoh: BBCA).")
            return
        self.var_cek_ticker.set(sym)
        cid = self._current_chat_id()
        period = self.var_cek_period.get() or "1y"
        modal = self._get_modal_rp()
        reg = self._regime_ctx()
        self.var_status.set(f"Menganalisis {sym} ({period}) — teknikal, fundamental, forensik, berita...")

        def _work() -> dict[str, Any]:
            detail = dm.inspect_ticker_detail(
                sym,
                modal,
                chat_id=cid,
                period=period,
                market_regime=reg,
            )
            chart_path = ""
            res = detail.get("res") or {}
            ind = res.get("ind")
            if detail.get("ok") and ind is not None and not getattr(ind, "empty", True):
                os.makedirs(config.TMP_DIR, exist_ok=True)
                out_png = os.path.join(config.TMP_DIR, f"desktop_chart_{sym}_{period}.png")
                plan = detail.get("plan") or {}
                levels = {
                    k: plan.get(k)
                    for k in ("entry", "sl", "tp")
                    if _safe_float(plan.get(k)) is not None
                }
                chart_path = chartmod.build_chart(
                    sym,
                    ind,
                    float((res.get("score") or {}).get("pct") or 0.0),
                    out_png,
                    levels=levels or None,
                )
            detail["chart_path"] = chart_path
            return detail

        self._run_async("cek", _work, "cek")

    # ------------------------------------------------------------------
    # Penerap Hasil ke UI (Main Thread)
    # ------------------------------------------------------------------

    def _apply_market(self, data: dict[str, Any]) -> None:
        self._last_market_pulse = data
        ihsg = data.get("ihsg") or {}
        reg = ihsg.get("regime") or {}
        breadth = data.get("breadth") or {}
        foreign = data.get("foreign") or {}
        news = data.get("news") or {}

        close_val = _safe_float(ihsg.get("close"))
        ret1d = _safe_float(ihsg.get("ret1d"))
        ret20d = _safe_float(ihsg.get("ret20d"))
        ihsg_clr = THEME["green"] if (ret1d or 0) > 0 else (THEME["red"] if (ret1d or 0) < 0 else THEME["fg"])
        _set_kpi(
            self.mk_ihsg,
            _fmt_num(close_val, 1) if close_val else "tidak tersedia",
            f"1H {_fmt_arrow_pct(ret1d)} · 20H {_fmt_pct(ret20d)}",
            color=ihsg_clr,
        )

        reg_label = str(reg.get("label") or "TIDAK TERSEDIA")
        scale = _safe_float(reg.get("exposure_scale"), 1.0) or 1.0
        reg_clr = THEME["green"] if scale >= 0.99 else (THEME["amber"] if scale >= 0.45 else THEME["red"])
        _set_kpi(
            self.mk_regime,
            f"{reg_label} ({scale:.1f}x)",
            str(reg.get("reason") or reg.get("summary") or "SMA50 vs SMA200 ^JKSE"),
            color=reg_clr,
        )
        self.var_regime_badge.set(
            f"IHSG {_fmt_num(close_val, 0)} ({_fmt_arrow_pct(ret1d)}) · Rezim {reg_label} ({scale:.1f}x)"
        )
        self.lbl_regime.configure(fg=reg_clr)

        if breadth.get("ok"):
            adv = breadth.get("advancing", 0)
            dec = breadth.get("declining", 0)
            beli = breadth.get("beli", 0)
            tot = breadth.get("total", 0)
            b_pct = _safe_float(breadth.get("beli_pct"))
            _set_kpi(
                self.mk_breadth,
                f"▲{adv} / ▼{dec}",
                f"BELI {beli}/{tot} ({b_pct:.1f}%)" if b_pct is not None else f"{tot} emiten",
                color=THEME["green"] if adv >= dec else THEME["red"],
            )
        else:
            _set_kpi(
                self.mk_breadth, "tidak tersedia", str(breadth.get("reason") or "belum /scan"),
                color=THEME["muted"],
            )

        if foreign.get("ok"):
            net_v = _safe_float(foreign.get("net_value"), 0.0) or 0.0
            f_clr = THEME["green"] if net_v > 0 else (THEME["red"] if net_v < 0 else THEME["fg"])
            _set_kpi(
                self.mk_foreign,
                str(foreign.get("label") or "-"),
                f"{foreign.get('n_rows', 0)} emiten BEI tercatat",
                color=f_clr,
            )
        else:
            _set_kpi(
                self.mk_foreign,
                "tidak tersedia",
                str(foreign.get("reason") or "data asing BEI offline"),
                color=THEME["muted"],
            )

        cond = str(data.get("condition") or "-")
        stale_txt = str(data.get("index_stale_age") or data.get("as_of_label") or "-")
        cond_clr = THEME["green"] if "KONDUSIF" in cond.upper() else (
            THEME["red"] if "WASPADA" in cond.upper() or "BERAT" in cond.upper() else THEME["amber"]
        )
        _set_kpi(
            self.mk_cond,
            cond,
            f"As of: {stale_txt}",
            color=cond_clr,
        )

        self._draw_sector_bars()

        lines = [
            f"KONDISI PASAR: {cond} ({data.get('as_of_label') or '-'})",
            f"Catatan Delay: {data.get('delay_note') or '-'}",
            "",
        ]
        for n in data.get("condition_notes") or []:
            lines.append(f"• {n}")
        lines.append("")
        if news and news.get("n"):
            npct = _safe_float(news.get("pct"), 0.0) or 0.0
            lines.append(
                f"SENTIMEN BERITA PASAR: {news.get('label', '-')} "
                f"(skor {npct:+.0f}%, {news.get('n', 0)} berita)"
            )
            for item in (news.get("details") or [])[:8]:
                title = item.get("title") or ""
                st = _news_item_tag(item)
                lines.append(f"  - [{st}] {title}")
        else:
            lines.append("SENTIMEN BERITA PASAR: tidak tersedia / RSS berita tidak terjangkau.")

        self._set_rich_text(self.txt_market, "\n".join(lines))
        self.var_status.set(
            f"Pasar diperbarui ({datetime.now().strftime('%H:%M:%S')} WIB) · Rezim {reg_label}"
        )

    def _draw_sector_bars(self) -> None:
        cv = self.canvas_sectors
        cv.delete("all")
        self._sector_rects = []
        w = max(360, cv.winfo_width())
        h = max(260, cv.winfo_height())

        sec_info = (self._last_market_pulse or {}).get("sectors") or {}
        if not sec_info.get("ok"):
            msg = sec_info.get("reason") or "Data indeks sektor BEI tidak tersedia (atau diblokir IP)."
            cv.create_text(
                w // 2, h // 2, text=msg, fill=THEME["muted"], font=FONT_SMALL, width=w - 40,
            )
            return

        rows = list(sec_info.get("sectors") or [])
        if not rows:
            cv.create_text(
                w // 2, h // 2, text="Tidak ada baris sektor IDX-IC.", fill=THEME["muted"],
                font=FONT_SMALL,
            )
            return

        max_abs = max(max(abs(float(r.get("pct") or 0.0)) for r in rows), 0.5)
        n = len(rows)
        top_pad = 14
        row_h = min(30, max(20, (h - top_pad * 2) // max(n, 1)))
        label_w = 158
        pct_w = 68
        chart_left = label_w + 10
        chart_right = w - pct_w - 14
        center_x = (chart_left + chart_right) / 2.0
        half_span = max(30.0, (chart_right - chart_left) / 2.0 - 4.0)

        cv.create_line(center_x, top_pad - 4, center_x, top_pad + n * row_h, fill=THEME["border"], width=1)
        for i, row in enumerate(rows):
            y0 = top_pad + i * row_h
            ym = y0 + row_h / 2.0
            pct = float(row.get("pct") or 0.0)
            full_name = str(row.get("name") or row.get("code") or "-")
            name = full_name[:22]
            clr = THEME["green"] if pct > 0 else (THEME["red"] if pct < 0 else THEME["muted"])

            cv.create_text(8, ym, text=name, anchor="w", fill=THEME["fg"], font=FONT_SMALL)
            bar_len = (abs(pct) / max_abs) * half_span
            if pct >= 0:
                x0, x1 = center_x, center_x + max(2.0, bar_len)
            else:
                x0, x1 = center_x - max(2.0, bar_len), center_x
            cv.create_rectangle(x0, y0 + 5, x1, y0 + row_h - 5, fill=clr, outline="")
            cv.create_text(
                w - 10, ym, text=f"{pct:+.2f}%", anchor="e", fill=clr, font=FONT_MONO_BOLD,
            )
            # rect area klik (label + bar + pct) untuk lompat ke filter sinyal
            self._sector_rects.append((0.0, float(y0), float(w), float(y0 + row_h), full_name))

    def _apply_watch(self, rows: list[dict[str, Any]]) -> None:
        self._last_watch_rows = rows or []
        self._render_watch_table()
        self._update_tab_badges()
        self._rebuild_quick_chips()
        for r in self._last_watch_rows:
            sym = r.get("ticker", "")
            for trig in r.get("triggers") or []:
                lbl = _strip_html(alertsmod.KIND_LABEL.get(trig, trig))
                self._push_alert("Sinyal / Alert", sym, lbl)
            for zn in r.get("zones") or []:
                lbl = _strip_html(alertsmod.ZONE_LABEL.get(zn, zn))
                self._push_alert("Zona Beli", sym, lbl)
        for al in dm.detect_action_alerts(self._prev_alert_state, self._last_watch_rows, self._last_port_rows):
            cat = "Exit / Tesis" if "TESIS" in al.get("kind", "") else "Sinyal / Alert"
            self._push_alert(cat, al.get("ticker", ""), f"{al.get('title', '')}: {al.get('message', '')}")

    def _render_watch_table(self) -> None:
        rows = self._last_watch_rows
        q = self.var_watch_search.get().strip().upper()
        filtered = [
            r for r in rows
            if not q or q in str(r.get("ticker", "")).upper() or q in str(r.get("sector", "")).upper()
        ]
        beli_n = sum(1 for r in filtered if (r.get("signal") or r.get("action")) == "BELI")
        zona_n = sum(1 for r in filtered if r.get("zones"))
        alert_n = sum(1 for r in filtered if r.get("triggers") or r.get("stale"))

        _set_kpi(
            self.wk_total,
            str(len(filtered)),
            f"dari {len(rows)} emiten" if rows else "ketik Ticker + Tambah di kiri atas",
            color=THEME["fg"],
        )
        _set_kpi(self.wk_beli, str(beli_n), "berstatus BELI", color=THEME["green"] if beli_n else THEME["muted"])
        _set_kpi(self.wk_zona, str(zona_n), "support/RSI/breakout", color=THEME["accent"] if zona_n else THEME["muted"])
        _set_kpi(self.wk_alert, str(alert_n), "alert aktif / data stale", color=THEME["amber"] if alert_n else THEME["muted"])

        table_rows: list[tuple[tuple[Any, ...], str | None]] = []
        for r in filtered:
            sig = r.get("signal") or r.get("action") or "-"
            tone: str | None = None
            if r.get("triggers") or r.get("zones"):
                tone = "info"
            elif sig == "BELI":
                tone = "buy"
            elif sig == "JUAL":
                tone = "sell"
            elif r.get("stale"):
                tone = "warn"

            alert_kinds = ",".join(sorted(r.get("alerts") or [])) or "-"
            notes = [
                _strip_html(alertsmod.ZONE_LABEL.get(z, z)) for z in (r.get("zones") or [])
            ] + [
                _strip_html(alertsmod.KIND_LABEL.get(t, t)) for t in (r.get("triggers") or [])
            ]
            if not notes:
                notes = list(r.get("reasons") or ([r["reason"]] if r.get("reason") else []))
            if r.get("stale"):
                notes.insert(0, "[STALE]")
            table_rows.append((
                (
                    r.get("ticker", "-"),
                    r.get("sector") or "-",
                    "ISSI" if r.get("syariah") else "-",
                    _fmt_num(r.get("price"), 0),
                    _fmt_arrow_pct(r.get("ret1d")),
                    f"{r['score_pct']:.0f}" if r.get("score_pct") is not None else "-",
                    sig,
                    _fmt_num(r.get("entry"), 0),
                    _fmt_num(r.get("sl"), 0),
                    _fmt_num(r.get("tp"), 0),
                    alert_kinds,
                    " · ".join(notes[:2]) or "-",
                ),
                tone,
            ))
        _populate_tree(self.tree_watch, table_rows)

    def _apply_sinyal(self, data: dict[str, Any]) -> None:
        rows = data.get("rows") or []
        self._last_sinyal_rows = rows
        self._update_tab_badges()
        counts = data.get("counts") or {}
        meta = data.get("meta") or {}
        sectors = ["Semua Sektor"] + list(data.get("sectors") or [])
        self.cb_sinyal_sector.configure(values=sectors)

        tot_scan = sum(int(v or 0) for v in counts.values()) or len(rows)
        _set_kpi(
            self.sk_scan_date,
            str(meta.get("trade_date") or "-"),
            f"total {tot_scan} emiten tersimpan",
        )
        _set_kpi(
            self.sk_matched,
            str(len(rows)),
            f"cocok: {data.get('total_matched', len(rows))}",
            color=THEME["accent"],
        )
        _set_kpi(self.sk_beli, str(counts.get("BELI", 0)), "sinyal BELI", color=THEME["green"])
        _set_kpi(self.sk_tahan, str(counts.get("TAHAN", 0)), "sinyal TAHAN", color=THEME["amber"])
        _set_kpi(self.sk_jual, str(counts.get("JUAL", 0)), "sinyal JUAL", color=THEME["red"])

        table_rows: list[tuple[tuple[Any, ...], str | None]] = []
        for r in rows:
            sig = str(r.get("signal") or "-")
            tone = "buy" if sig == "BELI" else ("sell" if sig == "JUAL" else None)
            score_v = _safe_float(r.get("rel_pct"))
            if score_v is None:
                score_v = _safe_float(r.get("score_pct"))
            rsi_v = _safe_float(r.get("rsi"))
            if rsi_v is None:
                rsi_v = _safe_float(r.get("rsi14"))
            vr = _safe_float(r.get("vol_ratio"))
            val20 = _safe_float(r.get("value_avg20"))
            liq_txt = f"{vr:.1f}x" if vr is not None else (f"{val20 / 1e9:.1f}M" if val20 else "-")
            q_mark = str(r.get("quality_mark") or "").strip()
            reason_txt = str(r.get("reason") or "-")
            if q_mark and q_mark != "❔":
                reason_txt = f"{q_mark} {reason_txt}"
            table_rows.append((
                (
                    r.get("ticker", "-"),
                    (r.get("name") or "-")[:24],
                    r.get("sector") or "-",
                    "ISSI" if r.get("syariah") else "-",
                    _fmt_num(r.get("price"), 0),
                    _fmt_arrow_pct(r.get("ret1d")),
                    f"{score_v:.1f}" if score_v is not None else "-",
                    sig,
                    f"{rsi_v:.1f}" if rsi_v is not None else "-",
                    liq_txt,
                    reason_txt,
                ),
                tone,
            ))
        _populate_tree(self.tree_sinyal, table_rows)
        self.var_status.set(f"Sinyal pasar dimuat: {len(rows)} baris ditampilkan.")

    def _apply_paket(self, data: dict[str, Any]) -> None:
        self._last_paket_res = data
        plans = data.get("plans") or []
        self._update_tab_badges()
        scale = float(data.get("scale") or 1.0)
        modal = float(data.get("modal") or 0.0)
        alloc = float(data.get("total_alloc_rp") or 0.0)
        cash_left = float(data.get("cash_left_rp") or 0.0)
        tot_risk = float(data.get("total_risk_rp") or 0.0)

        _set_kpi(
            self.pk_modal,
            _fmt_rp(modal),
            f"skala eksposur rezim: {scale:.1f}x",
            color=THEME["accent"],
        )
        _set_kpi(
            self.pk_alloc,
            _fmt_rp(alloc),
            f"{len(plans)} emiten · {(alloc / modal * 100.0):.1f}% modal" if modal > 0 else "-",
            color=THEME["green"] if plans else THEME["muted"],
        )
        _set_kpi(self.pk_cash, _fmt_rp(cash_left), "cadangan kas tersisa")
        _set_kpi(
            self.pk_risk,
            _fmt_rp(tot_risk),
            f"{(tot_risk / modal * 100.0):.2f}% risiko SL portofolio" if modal > 0 else "-",
            color=THEME["amber"] if tot_risk > 0 else THEME["muted"],
        )

        table_rows: list[tuple[tuple[Any, ...], str | None]] = []
        for p in plans:
            grade = str(p.get("grade") or "-")
            tone = "buy" if grade in ("A", "B") else ("warn" if p.get("warnings") else None)
            warns = "; ".join(p.get("warnings") or []) or str(p.get("reason") or "siap manual Stockbit")
            table_rows.append((
                (
                    grade,
                    p.get("ticker", "-"),
                    p.get("sector") or "-",
                    "ISSI" if p.get("syariah") else "-",
                    _fmt_num(p.get("entry"), 0),
                    _fmt_num(p.get("entry_pullback"), 0) if p.get("entry_pullback") else "-",
                    _fmt_num(p.get("sl"), 0),
                    _fmt_num(p.get("tp"), 0),
                    str(p.get("lots", 0)),
                    _fmt_rp(p.get("buy_cost")),
                    f"{_fmt_rp(p.get('real_risk'))} ({_fmt_pct(p.get('risk_pct'), signed=False)})",
                    warns,
                ),
                tone,
            ))
        _populate_tree(self.tree_paket, table_rows)

        rej = data.get("rejected") or {}
        if not data.get("ok"):
            self.lbl_paket_footer.configure(
                text=f"Info: {data.get('reason') or 'Paket belum tersedia — jalankan /scan dulu.'}",
                fg=THEME["amber"],
            )
        elif rej:
            rej_txt = ", ".join(f"{k}: {v}" for k, v in rej.items())
            self.lbl_paket_footer.configure(
                text=f"Kandidat gugur seleksi kualitas/likuiditas/batas: {rej_txt}",
                fg=THEME["muted"],
            )
        else:
            self.lbl_paket_footer.configure(
                text="Semua kandidat lolos veto kualitas & batas risiko. Gunakan 'Salin Order Stockbit' untuk panduan manual.",
                fg=THEME["green"],
            )

    def _apply_port(self, data: dict[str, Any]) -> None:
        rows = data.get("rows") or []
        summary = data.get("summary") or {}
        self._last_port_rows = rows
        self._last_port_summary = summary
        self._update_tab_badges()

        n_pos = int(summary.get("n_positions") or 0)
        priced_cnt = int(summary.get("priced_count") or 0)
        tot_val = summary.get("total_value_rp")
        tot_cost = float(summary.get("total_cost_rp") or 0.0)
        tot_pnl = summary.get("total_pnl_rp")
        pnl_pct = summary.get("total_pnl_pct")
        risk_rp = float(summary.get("risk_to_sl_rp") or 0.0)
        risk_pct = summary.get("risk_to_sl_pct")
        var95 = summary.get("var95_rp")
        es95 = summary.get("es95_rp")
        pos_lim = float(
            summary.get("max_pos_limit_pct")
            or ((summary.get("max_pos_pct") or 0.2) * 100.0)
        )
        sec_lim = float(
            summary.get("max_sec_limit_pct")
            or ((summary.get("max_sector_pct") or 0.4) * 100.0)
        )

        val_txt = "tidak tersedia" if (n_pos > 0 and priced_cnt == 0) else _fmt_rp(tot_val)
        _set_kpi(
            self.pt_val,
            val_txt,
            f"Cost: {_fmt_rp(tot_cost)} ({n_pos} posisi)",
        )
        if tot_pnl is None:
            _set_kpi(self.pt_pnl, "tidak tersedia" if n_pos > 0 else "-", "harga pasar belum tersedia", color=THEME["muted"])
        else:
            pnl_v = float(tot_pnl)
            pnl_clr = THEME["green"] if pnl_v > 0 else (THEME["red"] if pnl_v < 0 else THEME["fg"])
            _set_kpi(self.pt_pnl, _fmt_rp(pnl_v), f"{_fmt_arrow_pct(pnl_pct)}", color=pnl_clr)
        _set_kpi(
            self.pt_sl,
            _fmt_rp(risk_rp),
            f"{_fmt_pct(risk_pct, signed=False)} dari nilai/modal",
            color=THEME["amber"] if risk_rp > 0 else THEME["muted"],
        )
        var_txt = _fmt_rp(var95) if var95 is not None else "butuh >=120 bar"
        es_sub = f"ES95: {_fmt_rp(es95)} · Batas {pos_lim:.0f}%/{sec_lim:.0f}%" if es95 is not None else (
            f"Batas pos {pos_lim:.0f}% · sektor {sec_lim:.0f}%"
        )
        _set_kpi(self.pt_var, var_txt, es_sub, color=THEME["accent"] if var95 is not None else THEME["muted"])

        warns = list(summary.get("warnings") or [])
        if warns:
            self.lbl_port_warn.configure(text="⚠ " + " | ".join(warns[:3]))
            self.lbl_port_warn.pack(fill="x", padx=8, pady=(0, 6), before=self.tree_port.master)
        else:
            self.lbl_port_warn.pack_forget()

        table_rows: list[tuple[tuple[Any, ...], str | None]] = []
        for r in rows:
            thesis = str(r.get("thesis") or "TETAP").upper()
            pnl_v = _safe_float(r.get("pnl_net_pct"), 0.0) or 0.0
            tone = None
            if "GUGUR" in thesis:
                tone = "sell"
            elif "MELEMAH" in thesis or r.get("arb_warning"):
                tone = "warn"
            elif "MENGUAT" in thesis or pnl_v > 0:
                tone = "buy"

            eff_sl = r.get("effective_sl")
            sl_txt = f"{_fmt_num(eff_sl, 0)} ({r.get('sl_source') or 'ATR'})" if eff_sl else "-"
            tp_txt = (
                f"{_fmt_num(r.get('scale_out_tp1'), 0)}/{_fmt_num(r.get('tp'), 0)}"
                if r.get("scale_out_tp1") else _fmt_num(r.get("tp"), 0)
            )
            thesis_disp = (
                f"▼ {thesis}" if "GUGUR" in thesis else (f"▲ {thesis}" if "MENGUAT" in thesis else thesis)
            )
            reasons_txt = " · ".join(r.get("reasons") or []) or "-"
            table_rows.append((
                (
                    r.get("ticker", "-"),
                    r.get("sector") or "-",
                    str(r.get("lots", 0)),
                    _fmt_num(r.get("avg_price"), 0),
                    _fmt_num(r.get("price"), 0),
                    _fmt_arrow_pct(r.get("ret1d")),
                    _fmt_rp(r.get("value_rp")),
                    _fmt_rp(r.get("pnl_rp")),
                    _fmt_arrow_pct(r.get("pnl_net_pct")),
                    sl_txt,
                    tp_txt,
                    str(r.get("days_held") if r.get("days_held") is not None else "-"),
                    thesis_disp,
                    reasons_txt,
                ),
                tone,
            ))
            if "GUGUR" in thesis or "MELEMAH" in thesis:
                self._push_alert("Exit / Tesis", str(r.get("ticker", "")), f"Tesis {thesis}: {reasons_txt}")

        _populate_tree(self.tree_port, table_rows)
        self._rebuild_quick_chips()

    def _apply_jurnal(self, data: dict[str, Any]) -> None:
        self._last_jurnal_res = data
        kpis = data.get("kpis") or {}
        diag = data.get("diag") or {}

        n_exec = kpis.get("n_exec", 0)
        _set_kpi(
            self.jk_execs,
            str(n_exec),
            f"BELI {kpis.get('n_buys', 0)} · JUAL {kpis.get('n_sells', 0)}",
        )
        wr = kpis.get("win_rate_pct")
        _set_kpi(
            self.jk_wr,
            f"{wr:.1f}%" if wr is not None else "-",
            f"{kpis.get('n_closed', 0)} trade tertutup",
            color=THEME["green"] if (wr or 0) >= 50 else THEME["amber"],
        )
        pf = kpis.get("profit_factor")
        _set_kpi(
            self.jk_pf,
            f"{pf:.2f}" if pf is not None else "-",
            "rasio profit/rugi bersih",
            color=THEME["green"] if (pf or 0) >= 1.2 else THEME["fg"],
        )
        net_rp = _safe_float(kpis.get("total_net_pnl_rp"), 0.0) or 0.0
        _set_kpi(
            self.jk_pnl,
            _fmt_rp(net_rp),
            "total realisasi bersih",
            color=THEME["green"] if net_rp > 0 else (THEME["red"] if net_rp < 0 else THEME["fg"]),
        )
        avg_r = kpis.get("avg_r_multiple")
        alpha = kpis.get("mean_alpha_pct")
        mae = kpis.get("avg_mae_pct")
        _set_kpi(
            self.jk_alpha,
            f"{avg_r:+.2f}R" if avg_r is not None else "-",
            f"Alpha vs IHSG: {_fmt_pct(alpha)} · MAE: {_fmt_pct(mae)}",
            color=THEME["green"] if (alpha or 0) > 0 else THEME["muted"],
        )

        sections = [str(data.get("summary_text") or "")]
        if diag and diag.get("n_trades"):
            avg_r_diag = diag.get("avg_r_multiple")
            cap_ratio = diag.get("avg_capture_ratio")
            win_bench = diag.get("win_vs_bench_pct")
            r_str = f"{avg_r_diag:+.2f}R" if avg_r_diag is not None else "-"
            cap_str = f"{cap_ratio:.2f}" if cap_ratio is not None else "-"
            bench_str = f"{win_bench:.1f}%" if win_bench is not None else "-"
            sections.append(
                "\n--- DIAGNOSTIK MAE/MFE, R-MULTIPLE & ALPHA VS IHSG ---\n"
                f"Trade dianalisis : {diag.get('n_trades', 0)} "
                f"(OHLCV: {diag.get('n_with_ohlcv', 0)}, R: {diag.get('n_with_r', 0)})\n"
                f"Rata-rata R      : {r_str}\n"
                f"Rata MAE / MFE   : {_fmt_pct(diag.get('avg_mae_pct'))} / "
                f"{_fmt_pct(diag.get('avg_mfe_pct'))}\n"
                f"Capture Ratio    : {cap_str}\n"
                f"Alpha vs IHSG    : {_fmt_pct(diag.get('mean_alpha_pct'))} "
                f"(menang {bench_str})"
            )
            for note in diag.get("notes") or []:
                sections.append(f"  • {note}")
        sections.append("\n--- RIWAYAT EKSEKUSI TERAKHIR ---")
        sections.append(str(data.get("history_text") or "Belum ada eksekusi tercatat."))
        self._set_rich_text(self.txt_jurnal, "\n".join(sections))

    def _apply_cek(self, data: dict[str, Any]) -> None:
        self._last_cek_detail = data
        if not data.get("ok"):
            msg = data.get("reason") or "Data emiten tidak tersedia."
            self.lbl_cek_header.configure(text=msg, fg=THEME["red"])
            self._set_text(self.txt_cek, msg)
            self._chart_photo = None
            self.lbl_chart.configure(image="", text=msg)
            self._set_plan_cards(format_plan_levels(None))
            return

        sym = data.get("ticker", "")
        self._add_recent_ticker(sym)
        name = data.get("name") or "-"
        sec = data.get("sector") or "-"
        board = data.get("board") or "-"
        sy_tag = "SYARIAH (ISSI)" if data.get("syariah") else "Non-ISSI"
        res = data.get("res") or {}
        snap = res.get("snap") or {}
        cek = data.get("cek") or {}
        raw_ps = data.get("powerscore")
        ps = raw_ps if isinstance(raw_ps, dict) else {"rating": str(raw_ps or "-"), "pct": 0}
        power_text = str(data.get("power_text") or "")
        fund = data.get("fund")
        news = data.get("news") or {}
        plan = data.get("plan") or {}
        sc = data.get("scale_out") or {}
        arb = data.get("arb_risk") or {}

        verdict = str(cek.get("verdict") or "TUNGGU").upper()
        v_icon = "▲" if verdict == "LAYAK" else ("▼" if verdict == "HINDARI" else "●")
        v_clr = THEME["green"] if verdict == "LAYAK" else (THEME["red"] if verdict == "HINDARI" else THEME["amber"])

        ps_pct = _safe_float(ps.get("pct"), 0.0) or 0.0
        pts = cek.get("points")
        pts_txt = f" ({pts} poin)" if pts is not None else ""
        header_txt = (
            f"{sym} — {name}   |   {sec} ({board})   |   {sy_tag}   |   "
            f"Harga {_fmt_num(snap.get('close'), 0)} ({_fmt_arrow_pct(snap.get('ret1d'))})   |   "
            f"Verdict: {v_icon} {verdict}{pts_txt}   |   "
            f"PowerScore: {ps.get('rating', '-')} ({ps_pct:.0f}%)"
        )
        self.lbl_cek_header.configure(text=header_txt, fg=v_clr)
        self._set_plan_cards(format_plan_levels(plan))
        self._resize_chart_preview()

        lines: list[str] = []
        if data.get("quality_note"):
            lines.append(f"⚠ KUALITAS DATA: {data['quality_note']}")
        for v in cek.get("vetoes") or []:
            lines.append(f"⛔ VETO VERDICT: {v}")
        for rsn in cek.get("reasons") or []:
            lines.append(f"ℹ RINGKASAN VERDICT: {rsn}")
        lines.append(
            f"=== RENCANA MANUAL STOCKBIT (Modal {_fmt_rp(self._get_modal_rp())} · Periode {data.get('period')}) ==="
        )
        if plan.get("ok"):
            lines.append(
                f"Entry: {_fmt_num(plan.get('entry'), 0)} "
                f"(Pullback MA20: {_fmt_num(plan.get('entry_pullback'), 0)})  |  "
                f"Jangan Kejar > {_fmt_num(plan.get('chase_above'), 0)}"
            )
            lines.append(
                f"Stop-Loss: {_fmt_num(plan.get('sl'), 0)} ({_fmt_pct(plan.get('sl_dist_pct'))})  |  "
                f"Target TP: {_fmt_num(plan.get('tp'), 0)} (RR {plan.get('rr', 0):.2f}x)"
            )
            lines.append(
                f"Ukuran: {plan.get('lots', 0)} lot ({_fmt_rp(plan.get('buy_cost'))})  |  "
                f"Risiko SL: {_fmt_rp(plan.get('max_loss'))} "
                f"({_fmt_pct(plan.get('max_loss_pct'), signed=False)})  |  "
                f"Potensi TP: {_fmt_rp(plan.get('profit_tp'))}"
            )
            if sc.get("note"):
                lines.append(f"Scale-Out TP1: {sc['note']}")
        else:
            lines.append(f"Tidak bisa hitung sizing: {plan.get('skip_reason') or 'anggaran/harga belum memenuhi'}")

        for w in arb.get("warnings") or []:
            lines.append(f"⚠ {w}")
        for w in plan.get("warnings") or []:
            lines.append(f"⚠ {w}")

        lines.append("\n=== CEKLIST 6 BAGIAN BEI ===")
        for it in cek.get("items") or []:
            lines.append(f"• {it.get('label')}: [{it.get('status')}] {it.get('headline')} — {it.get('detail')}")

        if cek.get("bull_points"):
            lines.append("\nPRO / PENDUKUNG:")
            lines.extend(f"  + {p}" for p in cek["bull_points"])
        if cek.get("bear_points"):
            lines.append("RISIKO / CATATAN KRITIS:")
            lines.extend(f"  - {p}" for p in cek["bear_points"])

        if power_text:
            lines.append("\n=== PENILAIAN 3 PIN (POWERSCORE) ===")
            lines.append(power_text)

        lines.append("\n=== FUNDAMENTAL & FORENSIK ARUS KAS ===")
        if fund:
            fpct = _safe_float(fund.get("pct"), 0.0) or 0.0
            lines.append(
                f"Skor Fundamental: {fund.get('label', '-')} ({fpct:+.0f}% · "
                f"Mata uang laporan: {fund.get('currency', 'tidak diketahui')})"
            )
            for note in fund.get("notes") or []:
                lines.append(f"  • {note}")
        else:
            lines.append("Fundamental tidak tersedia dari Yahoo Finance.")

        lines.append("\n=== BERITA EMITEN ===")
        if news.get("n"):
            npct = _safe_float(news.get("pct"), 0.0) or 0.0
            lines.append(f"Sentimen {news.get('n')} berita: {news.get('label')} ({npct:+.0f}%)")
            for item in (news.get("details") or [])[:6]:
                lines.append(f"  - [{_news_item_tag(item)}] {item.get('title') or ''}")
        else:
            lines.append("Tidak ada berita terbaru / gagal memuat RSS.")

        self._set_rich_text(self.txt_cek, "\n".join(lines))
        self.var_status.set(f"Analisis {sym} selesai · {verdict} ({ps.get('rating', '-')})")

    def _resize_chart_preview(self) -> None:
        detail = self._last_cek_detail or {}
        path = detail.get("chart_path")
        if not path or not os.path.isfile(path) or ImageTk is None:
            return
        w = self.lbl_chart.winfo_width()
        h = self.lbl_chart.winfo_height()
        unmeasured = w < 120 or h < 120
        target_w = 540 if unmeasured else (w - 12)
        target_h = 390 if unmeasured else (h - 12)
        try:
            with Image.open(path) as im:
                ratio = min(target_w / im.width, target_h / im.height)
                if ratio <= 0:
                    return
                tw = max(120, int(im.width * ratio))
                th = max(120, int(im.height * ratio))
                resized = im.resize((tw, th), Image.Resampling.LANCZOS)
                self._chart_photo = ImageTk.PhotoImage(resized)
                self.lbl_chart.configure(image=self._chart_photo, text="")
        except Exception:
            pass
        if unmeasured and not self._closing:
            if self._chart_after_id:
                try:
                    self.root.after_cancel(self._chart_after_id)
                except Exception:
                    pass
            self._chart_after_id = self.root.after(120, self._resize_chart_preview)

    # ------------------------------------------------------------------
    # Interaksi Pengguna & Dialog
    # ------------------------------------------------------------------

    def _selected_ticker(self, tree: ttk.Treeview, col_index: int = 0) -> str | None:
        sel = tree.selection()
        if not sel:
            return None
        vals = tree.item(sel[0], "values")
        if not vals or len(vals) <= col_index:
            return None
        sym = str(vals[col_index]).strip().upper()
        return sym if sym and sym != "-" else None

    def _open_cek_ticker(self, sym: str | None) -> None:
        if not sym:
            return
        self.var_cek_ticker.set(sym.upper())
        # Isi penanda sementara agar <<NotebookTabChanged>> tidak memicu refresh ganda
        if not self._last_cek_detail:
            self._last_cek_detail = {"ticker": sym.upper(), "pending": True}
        self.notebook.select(6)
        self.refresh_cek()

    def _focus_cek_ticker(self) -> None:
        self.notebook.select(6)
        self.ent_cek.focus_set()
        self.ent_cek.select_range(0, "end")

    def _focus_active_search(self) -> None:
        idx = self.notebook.index(self.notebook.select())
        if idx == 1:
            self.ent_watch_search.focus_set()
        elif idx == 2:
            self.ent_sinyal_search.focus_set()
        else:
            self._focus_cek_ticker()

    def _watch_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_watch, 0))

    def _sinyal_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_sinyal, 0))

    def _paket_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_paket, 1))

    def _port_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_port, 0))

    def _alert_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_alerts, 2))

    def _add_watchlist(self, sym: str | None = None) -> None:
        code = (sym or self.var_watch_add.get()).strip().upper().replace(".JK", "")
        if not code:
            return
        cid = self._current_chat_id()
        ok, items = store_watchlist.watchlist_add(cid, code)
        self.var_watch_add.set("")
        self.var_status.set(
            f"{code} ditambahkan ke watchlist ({len(items)} emiten)"
            if ok else f"{code} sudah ada atau batas watchlist tercapai"
        )
        self.refresh_watch()

    def _remove_watchlist(self) -> None:
        sym = self._selected_ticker(self.tree_watch, 0)
        if not sym:
            return
        cid = self._current_chat_id()
        store_watchlist.watchlist_remove(cid, sym)
        store_watchlist.remove_alert(cid, sym)
        self.var_status.set(f"{sym} dihapus dari watchlist.")
        self.refresh_watch()

    def _watch_prefill_buy(self) -> None:
        sym = self._selected_ticker(self.tree_watch, 0)
        if not sym:
            return
        row = next((r for r in self._last_watch_rows if r.get("ticker") == sym), None)
        self._prefill_order_form(sym, "BELI", (row or {}).get("entry") or (row or {}).get("price"), 1)

    def _paket_prefill_buy(self) -> None:
        sym = self._selected_ticker(self.tree_paket, 1)
        if not sym:
            return
        plans = (self._last_paket_res or {}).get("plans") or []
        plan = next((p for p in plans if p.get("ticker") == sym), None)
        if plan:
            self._prefill_order_form(sym, "BELI", plan.get("entry"), plan.get("lots") or 1)

    def _port_prefill_sell(self) -> None:
        sym = self._selected_ticker(self.tree_port, 0)
        if not sym:
            return
        row = next((r for r in self._last_port_rows if r.get("ticker") == sym), None)
        if row:
            self._prefill_order_form(sym, "JUAL", row.get("price") or row.get("avg_price"), row.get("lots") or 1)

    def _prefill_order_form(self, sym: str, side: str, price: Any, lots: Any) -> None:
        self.var_port_ticker.set(sym)
        self.var_port_side.set(side)
        px = _safe_float(price)
        self.var_port_price.set(str(int(round(px))) if px else "")
        self.var_port_lots.set(str(max(1, int(_safe_float(lots, 1) or 1))))
        self.notebook.select(4)

    def _watch_edit_alerts(self) -> None:
        sym = self._selected_ticker(self.tree_watch, 0)
        if not sym:
            messagebox.showinfo("Atur Alert", "Pilih emiten di tabel Watchlist terlebih dahulu.")
            return
        cid = self._current_chat_id()
        current = set((store_watchlist.get_alerts(cid) or {}).get(sym) or ())
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Atur Kondisi Alert · {sym}")
        dlg.configure(bg=THEME["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        _enable_dark_titlebar(dlg)

        tk.Label(
            dlg, text=f"Aktifkan trigger alert otomatis untuk {sym}:",
            bg=THEME["bg"], fg=THEME["accent"], font=FONT_UI_BOLD, padx=14, pady=10,
        ).pack(anchor="w")

        vars_map: dict[str, tk.BooleanVar] = {}
        body = tk.Frame(dlg, bg=THEME["card"], padx=14, pady=10)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        for k in ALERT_KIND_ORDER:
            if k not in alertsmod.ALL_KINDS:
                continue
            v = tk.BooleanVar(value=k in current)
            vars_map[k] = v
            lbl = alertsmod.KIND_LABEL.get(k, k)
            ex = alertsmod.KIND_EXAMPLES.get(k, "")
            tk.Checkbutton(
                body,
                text=f"{lbl} ({ex})" if ex else lbl,
                variable=v,
                bg=THEME["card"],
                fg=THEME["fg"],
                selectcolor=THEME["bg_elev"],
                activebackground=THEME["card"],
                activeforeground=THEME["fg"],
                font=FONT_SMALL,
                anchor="w",
            ).pack(fill="x", pady=2)

        btns = tk.Frame(dlg, bg=THEME["bg"], padx=12, pady=8)
        btns.pack(fill="x")

        def _save() -> None:
            chosen = {k for k, var in vars_map.items() if var.get()}
            if chosen:
                store_watchlist.set_alert(cid, sym, chosen)
            else:
                store_watchlist.remove_alert(cid, sym)
            dlg.destroy()
            self.refresh_watch()

        ttk.Button(btns, text="Simpan", style="Accent.TButton", command=_save).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Batal", style="Ghost.TButton", command=dlg.destroy).pack(side="right")

    def _open_risk_limit_dialog(self) -> None:
        cid = self._current_chat_id()
        cur_pos, cur_sec = store_portfolio.get_risk_limits(cid)
        pos_in = simpledialog.askfloat(
            "Batas Risiko Posisi",
            "Maksimum satu posisi (% dari modal acuan, rentang 5 - 50):",
            initialvalue=round(cur_pos * 100, 1),
            minvalue=5.0,
            maxvalue=50.0,
            parent=self.root,
        )
        if pos_in is None:
            return
        sec_in = simpledialog.askfloat(
            "Batas Risiko Sektor",
            "Maksimum satu sektor (% dari modal acuan, rentang 10 - 80):",
            initialvalue=round(cur_sec * 100, 1),
            minvalue=10.0,
            maxvalue=80.0,
            parent=self.root,
        )
        if sec_in is None:
            return
        try:
            p_clean, s_clean = riskmod.validate_limits(pos_in / 100.0, sec_in / 100.0)
            store_portfolio.set_risk_limits(cid, p_clean, s_clean)
            self.var_status.set(f"Batas risiko disimpan: Posisi {p_clean*100:.1f}%, Sektor {s_clean*100:.1f}%")
            self.refresh_port()
            self.refresh_paket()
        except ValueError as exc:
            messagebox.showerror("Batas Risiko Tidak Valid", str(exc), parent=self.root)

    def _submit_execution(self) -> None:
        sym = self.var_port_ticker.get().strip().upper().replace(".JK", "")
        side = self.var_port_side.get().strip().upper()
        raw_px = self.var_port_price.get().strip()
        px = textutil.parse_rupiah(raw_px)
        if px is None:
            px = _safe_float(raw_px.replace(".", "").replace(",", ""))
        lots = _safe_float(self.var_port_lots.get().strip())
        notes = self.var_port_notes.get().strip()

        if not sym or px is None or px <= 0 or lots is None or int(lots) <= 0:
            messagebox.showerror("Input Tidak Lengkap", "Isi kode ticker, harga valid > 0, dan lot >= 1.")
            return
        n_lots = int(lots)
        cid = self._current_chat_id()

        if side == "JUAL":
            val = n_lots * 100 * px
            if not messagebox.askyesno(
                "Konfirmasi Catat Jual Manual",
                f"Catat JUAL {sym} {n_lots} lot @ {_fmt_num(px, 0)} (nilai bruto {_fmt_rp(val)})?\n\n"
                "Catatan ini hanya memperbarui jurnal & posisi lokal — eksekusi order tetap di Stockbit.",
                parent=self.root,
            ):
                return
        try:
            record_desktop_execution(
                cid,
                sym,
                side,
                n_lots,
                px,
                note=notes,
            )
        except Exception as exc:
            messagebox.showerror(f"Gagal Catat {side}", str(exc), parent=self.root)
            return

        self.var_port_notes.set("")
        self.var_status.set(f"Eksekusi {side} {sym} {n_lots} lot @ {_fmt_num(px, 0)} tercatat.")
        self.refresh_port()
        self.refresh_jurnal()

    # ------------------------------------------------------------------
    # Context Menu & Clipboard / Export
    # ------------------------------------------------------------------

    def _show_popup(self, event: tk.Event, items: list[tuple[str, Callable[[], None]]]) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=THEME["card"], fg=THEME["fg"],
                       activebackground=THEME["accent_soft"], activeforeground=THEME["accent"])
        for label, cb in items:
            menu.add_command(label=label, command=cb)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _watch_context_menu(self, event: tk.Event) -> None:
        iid = self.tree_watch.identify_row(event.y)
        if not iid:
            return
        self.tree_watch.selection_set(iid)
        sym = self._selected_ticker(self.tree_watch, 0)
        self._show_popup(event, [
            ("🔍 Cek Emiten Lengkap", self._watch_open_cek),
            ("🔔 Atur Alert Harga/Indikator", self._watch_edit_alerts),
            ("💼 Siapkan Catat BELI di Portofolio", self._watch_prefill_buy),
            ("🎯 Lihat di Sinyal Pasar", lambda: self._watch_goto_sinyal(sym)),
            ("🗑 Hapus dari Watchlist", self._remove_watchlist),
        ])

    def _sinyal_context_menu(self, event: tk.Event) -> None:
        iid = self.tree_sinyal.identify_row(event.y)
        if not iid:
            return
        self.tree_sinyal.selection_set(iid)
        sym = self._selected_ticker(self.tree_sinyal, 0)
        self._show_popup(event, [
            ("🔍 Cek Emiten Lengkap", self._sinyal_open_cek),
            ("⭐ Tambah ke Watchlist", lambda: self._add_watchlist(sym)),
            ("📦 Buka Paket Beli", lambda: self._goto_tab(3, self.refresh_paket)),
            ("⭐ Buka Watchlist", lambda: self._goto_tab(1, self.refresh_watch)),
        ])

    def _paket_context_menu(self, event: tk.Event) -> None:
        iid = self.tree_paket.identify_row(event.y)
        if not iid:
            return
        self.tree_paket.selection_set(iid)
        sym = self._selected_ticker(self.tree_paket, 1)
        self._show_popup(event, [
            ("🔍 Cek Emiten Lengkap", self._paket_open_cek),
            ("💼 Siapkan Catat BELI di Portofolio", self._paket_prefill_buy),
            ("📋 Salin Semua Order Stockbit", self._copy_paket_orders),
            ("⭐ Tambah ke Watchlist", lambda: self._add_watchlist(sym)),
        ])

    def _port_context_menu(self, event: tk.Event) -> None:
        iid = self.tree_port.identify_row(event.y)
        if not iid:
            return
        self.tree_port.selection_set(iid)
        sym = self._selected_ticker(self.tree_port, 0)
        self._show_popup(event, [
            ("🔍 Cek Emiten Lengkap", self._port_open_cek),
            ("💰 Siapkan Form JUAL", self._port_prefill_sell),
            ("⭐ Tambah ke Watchlist", lambda: self._add_watchlist(sym)),
            ("🔔 Buka Alert & Riwayat", lambda: self._goto_tab(7)),
        ])

    def _copy_text(self, text: str, status_msg: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.var_status.set(status_msg)

    def _copy_paket_orders(self) -> None:
        plans = (self._last_paket_res or {}).get("plans") or []
        txt = dm.format_order_ticket_text(plans)
        self._copy_text(txt, f"Tiket order Stockbit ({len(plans)} emiten) disalin ke clipboard.")

    def _copy_cek_order(self) -> None:
        plan = (self._last_cek_detail or {}).get("plan") or {}
        if not plan.get("ok"):
            messagebox.showinfo("Salin Order", "Rencana beli belum valid untuk emiten ini.")
            return
        txt = dm.format_order_ticket_text([plan])
        self._copy_text(txt, f"Tiket order {plan.get('ticker')} disalin ke clipboard.")

    def _cek_add_watchlist(self) -> None:
        sym = (self._last_cek_detail or {}).get("ticker") or self.var_cek_ticker.get()
        self._add_watchlist(sym)

    def _save_cek_chart_png(self) -> None:
        src = (self._last_cek_detail or {}).get("chart_path")
        if not src or not os.path.isfile(src):
            messagebox.showinfo("Simpan Chart", "Jalankan Cek Lengkap terlebih dahulu.")
            return
        sym = (self._last_cek_detail or {}).get("ticker") or "CHART"
        dest = filedialog.asksaveasfilename(
            parent=self.root,
            title="Simpan Chart PNG",
            defaultextension=".png",
            initialfile=f"SahamBot_{sym}.png",
            filetypes=[("PNG Image", "*.png")],
        )
        if dest:
            shutil.copy2(src, dest)
            self.var_status.set(f"Chart disimpan ke {dest}")

    def _export_tree_csv(self, default_name: str, headers: list[str], rows: list[list[Any]]) -> None:
        if not rows:
            messagebox.showinfo("Ekspor CSV", "Belum ada baris data untuk diekspor.")
            return
        dest = filedialog.asksaveasfilename(
            parent=self.root,
            title="Ekspor ke CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV File", "*.csv")],
        )
        if dest:
            dm.export_rows_to_csv(dest, headers, rows)
            self.var_status.set(f"Diekspor ke CSV: {dest}")

    def _export_watch_csv(self) -> None:
        rows = [list(self.tree_watch.item(i, "values")) for i in self.tree_watch.get_children("")]
        hdr = [self.tree_watch.heading(c, "text") for c in self.tree_watch["columns"]]
        self._export_tree_csv("sahambot_watchlist.csv", hdr, rows)

    def _export_sinyal_csv(self) -> None:
        rows = [list(self.tree_sinyal.item(i, "values")) for i in self.tree_sinyal.get_children("")]
        hdr = [self.tree_sinyal.heading(c, "text") for c in self.tree_sinyal["columns"]]
        self._export_tree_csv("sahambot_sinyal.csv", hdr, rows)

    def _export_paket_csv(self) -> None:
        rows = [list(self.tree_paket.item(i, "values")) for i in self.tree_paket.get_children("")]
        hdr = [self.tree_paket.heading(c, "text") for c in self.tree_paket["columns"]]
        self._export_tree_csv("sahambot_paket.csv", hdr, rows)

    def _export_port_csv(self) -> None:
        rows = [list(self.tree_port.item(i, "values")) for i in self.tree_port.get_children("")]
        hdr = [self.tree_port.heading(c, "text") for c in self.tree_port["columns"]]
        self._export_tree_csv("sahambot_portofolio.csv", hdr, rows)

    def _export_jurnal_csv(self) -> None:
        execs = (self._last_jurnal_res or {}).get("execs") or []
        headers = [
            "Tanggal",
            "Ticker",
            "Sisi",
            "Lot",
            "Harga Eksekusi",
            "Harga Sinyal",
            "Slippage %",
            "PnL Bersih Rp",
            "PnL %",
            "Sumber",
        ]
        rows = [
            [
                e.get("trade_date") or e.get("created_at") or "",
                e.get("ticker", ""),
                e.get("side", ""),
                e.get("lots", 0),
                e.get("exec_price", 0),
                e.get("signal_price", ""),
                e.get("slippage_pct", ""),
                e.get("net_pnl", ""),
                e.get("net_pnl_pct", ""),
                e.get("source", ""),
            ]
            for e in execs
        ]
        self._export_tree_csv("sahambot_jurnal.csv", headers, rows)

    # ------------------------------------------------------------------
    # Quick Chips, Alerts Tab & Tray
    # ------------------------------------------------------------------

    def _rebuild_quick_chips(self) -> None:
        for child in self.frm_cek_chips.winfo_children():
            child.destroy()
        tickers: list[str] = []
        for r in self._last_port_rows + self._last_watch_rows:
            t = str(r.get("ticker") or "").upper()
            if t and t not in tickers:
                tickers.append(t)
        if not tickers:
            tickers = ["BBCA", "BBRI", "TLKM", "ASII"]
        tk.Label(
            self.frm_cek_chips, text="Akses Cepat:", bg=THEME["bg"], fg=THEME["dim"],
            font=FONT_SMALL,
        ).pack(side="left", padx=(0, 6))
        for sym in tickers[:10]:
            tk.Button(
                self.frm_cek_chips,
                text=sym,
                bg=THEME["card"],
                fg=THEME["fg"],
                activebackground=THEME["accent_soft"],
                activeforeground=THEME["accent"],
                relief="flat",
                bd=0,
                padx=8,
                pady=2,
                font=FONT_SMALL_BOLD,
                cursor="hand2",
                command=lambda s=sym: self._open_cek_ticker(s),
            ).pack(side="left", padx=(0, 4))

    def _push_alert(self, category: str, ticker: str, message: str) -> None:
        key = f"{category}|{ticker}|{message}"
        if key in self._seen_alert_keys:
            return
        self._seen_alert_keys.add(key)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._alert_history.insert(0, {
            "time": ts, "kind": category, "ticker": ticker, "message": message,
        })
        self._alert_history = self._alert_history[:250]
        self._render_alerts_table()
        if not hasattr(self, "var_toast") or self.var_toast.get():
            send_windows_toast(f"{category}: {ticker}", message)

    def _render_alerts_table(self) -> None:
        filt = self.var_alert_filter.get()
        rows = [
            a for a in self._alert_history
            if filt == "Semua" or a.get("kind") == filt
        ]
        self.notebook.tab(7, text=f"🔔 Alert ({len(self._alert_history)})")
        table_rows: list[tuple[tuple[Any, ...], str | None]] = []
        for a in rows:
            cat = str(a.get("kind") or "")
            tone = "sell" if "Exit" in cat else ("info" if "Zona" in cat else "warn")
            table_rows.append((
                (a.get("time", ""), cat, a.get("ticker", ""), a.get("message", "")),
                tone,
            ))
        _populate_tree(self.tree_alerts, table_rows)

    def _copy_alerts_text(self) -> None:
        lines = [
            f"[{a['time']}] {a['kind']} {a['ticker']}: {a['message']}"
            for a in self._alert_history
        ]
        self._copy_text("\n".join(lines) or "Belum ada riwayat alert.", "Riwayat alert disalin.")

    def _clear_alerts(self) -> None:
        if not self._alert_history:
            self.var_status.set("Riwayat alert sudah kosong.")
            return
        if not messagebox.askyesno(
            "Bersihkan Riwayat",
            f"Hapus {len(self._alert_history)} entri riwayat alert? Tindakan ini tidak bisa dibatalkan.",
            parent=self.root,
        ):
            return
        self._alert_history.clear()
        self._seen_alert_keys.clear()
        self._render_alerts_table()
        self.var_status.set("Riwayat alert dibersihkan.")

    def _set_text(self, widget: scrolledtext.ScrolledText, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _setup_tray(self) -> None:
        if pystray is None:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Buka SahamBot Desktop", lambda *_: self._ui_queue.put(("_tray_show", None)), default=True),
                pystray.MenuItem("Refresh Semua Data", lambda *_: self._ui_queue.put(("_tray_refresh", None))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Keluar", lambda *_: self._ui_queue.put(("_tray_quit", None))),
            )
            self._tray_icon = pystray.Icon(
                "SahamBotDesktop", create_tray_icon_image(64), "SahamBot Desktop", menu,
            )
            threading.Thread(target=self._tray_icon.run, name="desktop-tray", daemon=True).start()
        except Exception:
            self._tray_icon = None

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close_or_tray(self) -> None:
        if self._tray_icon is not None:
            self.root.withdraw()
            if not hasattr(self, "var_toast") or self.var_toast.get():
                send_windows_toast("SahamBot Desktop", "Berjalan di latar belakang (System Tray).")
        else:
            self._quit_app()

    def _quit_app(self) -> None:
        self._closing = True
        self._save_ui_state()
        for attr in (
            "_pump_after_id",
            "_auto_after_id",
            "_chart_after_id",
            "_clock_after_id",
            "_status_after_id",
        ):
            aid = getattr(self, attr, None)
            if aid:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entrypoint Aplikasi & Kunci Single-Instance Windows
# ---------------------------------------------------------------------------

def record_desktop_execution(
    chat_id: int | str,
    ticker: str,
    side: str,
    lots: int,
    price: float,
    *,
    trade_date: str | None = None,
    sl: float | None = None,
    tp: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Catat eksekusi BELI/JUAL manual Stockbit ke SQLite sesuai kontrak `store_portfolio`."""
    sym = str(ticker or "").strip().upper().replace(".JK", "")
    sd = str(side or "").strip().upper()
    tdate = trade_date or datetime.now().strftime("%Y-%m-%d")
    src = f"desktop:{note.strip()[:40]}" if note and note.strip() else "desktop"
    if sd == "JUAL":
        return store_portfolio.record_execution_sell(
            chat_id, sym, int(lots), float(price), tdate, source=src,
        )
    return store_portfolio.record_execution_buy(
        chat_id, sym, int(lots), float(price), tdate, sl=sl, tp=tp, source=src,
    )


def _acquire_single_instance_lock() -> Any:
    """Cegah dua instance SahamBot Desktop berjalan bersamaan di Windows."""
    if sys.platform != "win32":
        return None
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\SahamBotDesktopTerminalMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        return mutex
    except Exception:
        return None


def main() -> int:
    """Jalankan SahamBot Desktop UI."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    lock = _acquire_single_instance_lock()
    if lock is False:
        messagebox.showinfo("SahamBot Desktop", "Aplikasi SahamBot Desktop sudah berjalan.")
        return 0

    root = tk.Tk()
    SahamBotDesktopApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

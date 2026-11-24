"""SahamBot Desktop (Windows) — Aplikasi Native Pantau Pasar, Sinyal, Paket, & Tesis.

Jalankan langsung:
    SahamBot.exe                  (hasil build PyInstaller)
atau via Python:
    .venv\\Scripts\\python.exe desktop.py

Fitur:
- System Tray Windows (menutup jendela akan minimize ke tray dan tetap memantau di background).
- 8 Tab lengkap: Kondisi Pasar (/pasar), Radar Watchlist (BUY/SELL), Sinyal & Scan BEI (/scan, /sinyal),
  Paket Beli (/paket), Portofolio & Tesis (/portofolio), Jurnal & Alpha (/jurnal), Cek Emiten (/cek + grafik),
  dan Log Alert + Windows Toast Notification.
- UI dark "trading terminal": custom title bar (drag, minimize, maximize, close ke tray),
  kartu KPI IHSG/rezim/kondisi, tab segmented, tabel zebra, dan panel teks dengan pewarnaan.
"""
from __future__ import annotations

import html
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

from PIL import Image, ImageDraw
import pystray

from core import (
    chart as chartmod,
    desktop_monitor as dm,
    reports,
    store_portfolio,
    store_watchlist,
    textutil,
)

# ---- Design tokens (dark trading-terminal theme) ----
BG = "#0a0f1c"        # window background
PANEL = "#0e1526"     # title bar / status bar / tab bar
CARD = "#131d33"      # cards
CARD2 = "#1a2745"     # inputs, hover, tree zebra
TEXTBG = "#0d1626"    # text panels & tree
BORDER = "#243354"    # 1px card borders
FG = "#e8eefb"
MUTED = "#8b9bb8"
ACCENT = "#3b82f6"
ACCENT_D = "#2456c9"
GREEN = "#34d399"
RED = "#f87171"
AMBER = "#fbbf24"
BLUE = "#38bdf8"

# Chip (badge) styles: nama -> (background, foreground)
CHIPS = {
    "green": ("#0d2b21", GREEN),
    "red": ("#331720", RED),
    "amber": ("#33270f", AMBER),
    "blue": ("#0e2a3d", BLUE),
    "gray": ("#1b2745", MUTED),
    "accent": ("#14305c", "#9ec5ff"),
}

_KIND_CHIP = [
    ("BULL", "green"), ("KONSTRUKTIF", "green"), ("MENGUAT", "green"), ("LAYAK", "green"), ("BELI", "green"),
    ("POSITIF", "green"),
    ("BEAR", "red"), ("GUGUR", "red"), ("HINDARI", "red"), ("JUAL", "red"), ("NEGATIF", "red"),
    ("DEFENSIVE", "amber"), ("WASPADA", "amber"), ("TUNGGU", "amber"), ("MELEMAH", "amber"), ("CAMPURAN", "amber"),
    ("TIDAK_CUKUP_DATA", "gray"),
    ("SIAP BELI", "blue"),
]


def _chip_for(text: str | None) -> str:
    """Pilih warna chip berdasarkan kata kunci status (BELI=green, JUAL=red, dst.)."""
    t = (text or "").upper()
    for kw, chip in _KIND_CHIP:
        if kw in t:
            return chip
    return "accent"


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", text or ""))


def create_tray_icon_image(size: int = 64) -> Image.Image:
    """Buat ikon SahamBot sederhana (grafik naik hijau di latar navy)."""
    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=12, outline=(56, 189, 248, 255), width=3)
    pts = [(10, 46), (24, 34), (34, 40), (52, 16)]
    draw.line(pts, fill=(34, 197, 94, 255), width=5)
    draw.ellipse([47, 11, 57, 21], fill=(34, 197, 94, 255))
    return img


def send_windows_toast(title: str, message: str) -> None:
    """Kirim notifikasi toast Windows non-blocking (fail-soft, tanpa paket tambahan)."""
    if sys.platform != "win32":
        return

    def _run():
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        safe_t = str(title).replace("'", "''")[:80]
        safe_m = str(message).replace("'", "''")[:180]
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.BalloonTipIcon = 'Info';"
            f"$n.BalloonTipTitle = '{safe_t}';"
            f"$n.BalloonTipText = '{safe_m}';"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(5000);"
            "Start-Sleep -Milliseconds 5500;"
            "$n.Dispose()"
        )
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _fmt_rp(val: float | None) -> str:
    if val is None:
        return "-"
    return f"Rp{val:,.0f}".replace(",", ".")


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:+.2f}%"


class Switch(tk.Canvas):
    """Toggle switch bergaya pill (kanvas kecil) untuk pengaturan ON/OFF."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None, bg: str = CARD, **kw):
        super().__init__(parent, width=36, height=20, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2", **kw)
        self.var = variable
        self.command = command
        self._draw()
        self.var.trace_add("write", lambda *_: self._draw())
        self.bind("<Button-1>", self._toggle)

    def _toggle(self, _e) -> None:
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self) -> None:
        self.delete("all")
        on = bool(self.var.get())
        pill = ACCENT if on else BORDER
        r = 9
        self.create_oval(1, 1, 1 + 2 * r, 1 + 2 * r, fill=pill, outline="")
        self.create_oval(35 - 2 * r, 1, 35, 1 + 2 * r, fill=pill, outline="")
        self.create_rectangle(1 + r, 1, 35 - r, 1 + 2 * r, fill=pill, outline="")
        kx = 19 if on else 3
        self.create_oval(kx, 3, kx + 14, 17, fill="#ffffff" if on else MUTED, outline="")


class SahamBotDesktopApp:
    """Antarmuka desktop Tkinter + System Tray untuk seluruh fitur SahamBot."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SahamBot Desktop — Radar Pasar & Sinyal BEI")
        self.root.configure(bg=BORDER)
        self.root.overrideredirect(True)

        win_w, win_h = 1360, 860
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x0 = max(0, (sw - win_w) // 2)
        y0 = max(0, (sh - win_h) // 3)
        self.root.geometry(f"{win_w}x{win_h}+{x0}+{y0}")
        self._normal_geo = self.root.geometry()
        self.root.minsize(1180, 720)
        self._maxed = False
        self._drag_off: tuple[int, int] | None = None
        self._rs: tuple | None = None

        try:
            from PIL import ImageTk
            self._icon_img = ImageTk.PhotoImage(create_tray_icon_image(64))
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            self._icon_img = None

        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(fill="both", expand=True, padx=1, pady=1)

        self.ui_queue: queue.Queue = queue.Queue()
        self.prev_alert_state: dict[str, str] = {}
        self._refresh_busy = False
        self._scan_busy = False
        self._after_id: str | None = None
        self._chart_img: tk.PhotoImage | None = None
        self._tray_icon: pystray.Icon | None = None
        self._tray_notified = False
        self._last_regime: dict | None = None

        self.profile_var = tk.StringVar(value=dm.DEFAULT_PROFILE_ID)
        self.modal_var = tk.StringVar(value="10000000")
        self.interval_var = tk.StringVar(value="60")
        self.auto_var = tk.BooleanVar(value=True)
        self.toast_var = tk.BooleanVar(value=True)
        self.sinyal_filter_var = tk.StringVar(value="BELI")
        self.sinyal_quality_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Siap memantau pasar.")
        self.ihsg_close_var = tk.StringVar(value="—")
        self.ihsg_chg_var = tk.StringVar(value="—")
        self.regime_var = tk.StringVar(value="—")
        self.regime_sub_var = tk.StringVar(value="")
        self.cond_var = tk.StringVar(value="—")
        self.cond_sub_var = tk.StringVar(value="")
        self.update_var = tk.StringVar(value="—")
        self.auto_chip_var = tk.StringVar(value="AUTO-PANTAU ON")

        self._setup_style()
        self._build_chrome()
        self._build_layout()
        self._build_edges()
        self._load_initial_profile()
        self._setup_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)
        self.root.after(150, self._poll_queue)
        self.root.after(400, self.trigger_refresh_all)

    # ------------------------------------------------------------------ style
    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        s = style
        s.configure(".", background=BG, foreground=FG, fieldbackground=CARD2, font=("Segoe UI", 9))
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("Panel.TFrame", background=PANEL)

        s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 9))
        s.configure("Card.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 9))
        s.configure("Panel.TLabel", background=PANEL, foreground=FG, font=("Segoe UI", 9))
        s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Card.Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 8))
        s.configure("Panel.Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8))
        s.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 15, "bold"))
        s.configure("Card.Title.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 15, "bold"))
        s.configure("H2.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 11, "bold"))
        s.configure("KpiLabel.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 8, "bold"))
        s.configure("Empty.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 10, "italic"))

        for name, (bgc, fgc) in CHIPS.items():
            s.configure(f"Chip.{name}.TLabel", background=bgc, foreground=fgc,
                        font=("Segoe UI", 9, "bold"), padding=(8, 3))

        s.configure("TButton", background=CARD2, foreground=FG, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, font=("Segoe UI", 9),
                    padding=(10, 5), relief="flat")
        s.map("TButton",
              background=[("active", "#223257"), ("pressed", "#2a3d66"), ("disabled", CARD)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT_D, foreground="#ffffff", bordercolor=ACCENT_D,
                    lightcolor=ACCENT_D, darkcolor=ACCENT_D, font=("Segoe UI", 9, "bold"),
                    padding=(12, 5), relief="flat")
        s.map("Accent.TButton",
              background=[("active", ACCENT), ("pressed", "#1e40af"), ("disabled", "#1e3a5f")],
              foreground=[("disabled", MUTED)])
        s.configure("Danger.TButton", background=CARD2, foreground=RED, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, font=("Segoe UI", 9),
                    padding=(10, 5), relief="flat")
        s.map("Danger.TButton", background=[("active", "#3a1a22"), ("pressed", "#4a2028")])

        s.configure("TEntry", fieldbackground=CARD2, foreground=FG, bordercolor=BORDER,
                    relief="flat", padding=4, insertcolor=FG)
        s.map("TEntry", bordercolor=[("focus", ACCENT)],
              fieldbackground=[("focus", "#1b2a4a")])
        s.configure("TCombobox", fieldbackground=CARD2, background=CARD2, foreground=FG,
                    arrowcolor=MUTED, bordercolor=BORDER, relief="flat", padding=4)
        s.map("TCombobox",
              fieldbackground=[("readonly", CARD2), ("focus", "#1b2a4a")],
              bordercolor=[("focus", ACCENT)],
              selectbackground=[("readonly", CARD)],
              selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_D)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

        s.configure("TCheckbutton", background=CARD, foreground=FG, font=("Segoe UI", 9),
                    focuscolor=CARD, indicatorcolor=ACCENT, selectcolor=ACCENT)
        s.map("TCheckbutton",
              background=[("active", CARD)],
              foreground=[("active", FG), ("disabled", MUTED)],
              indicatorcolor=[("selected", ACCENT), ("active", CARD2)])

        s.configure("Treeview", background=TEXTBG, foreground=FG, fieldbackground=TEXTBG,
                    rowheight=30, font=("Segoe UI", 9), bordercolor=BORDER, relief="flat")
        s.configure("Treeview.Heading", background="#0c1424", foreground=MUTED,
                    font=("Segoe UI", 8, "bold"), relief="flat", padding=(6, 6))
        s.map("Treeview",
              background=[("selected", ACCENT_D)],
              foreground=[("selected", "#ffffff")])
        s.map("Treeview.Heading", background=[("active", CARD2)])

        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=CARD2, troughcolor=CARD,
                        arrowcolor=MUTED, bordercolor=CARD, relief="flat", width=10)
            s.map(f"{orient}.TScrollbar",
                  background=[("active", "#2a3d66"), ("pressed", "#2a3d66")])

    # ------------------------------------------------------------- chrome
    def _build_chrome(self) -> None:
        chrome = tk.Frame(self.main, bg=PANEL, height=42)
        chrome.pack(fill="x")
        chrome.pack_propagate(False)
        tk.Frame(self.main, bg=BORDER, height=1).pack(fill="x")

        logo = tk.Canvas(chrome, width=24, height=24, bg=PANEL, highlightthickness=0, bd=0)
        logo.create_rectangle(1, 1, 23, 23, outline=BLUE, width=2)
        logo.create_line(5, 17, 10, 12, 13, 14, 19, 6, fill=GREEN, width=2)
        logo.create_oval(17, 4, 21, 8, fill=GREEN, outline="")
        logo.pack(side="left", padx=(12, 8), pady=9)

        brand = tk.Frame(chrome, bg=PANEL)
        brand.pack(side="left")
        tk.Label(brand, text="SahamBot", bg=PANEL, fg=FG, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(brand, text="Desktop · Radar Pasar & Sinyal BEI", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")

        self._chrome_drag_widgets = [chrome, logo, brand] + brand.winfo_children()
        for w in self._chrome_drag_widgets:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Double-Button-1>", self._toggle_max)

        self.btn_close = self._chrome_button(chrome, "✕", hover="#e81123", command=self._minimize_to_tray)
        self.btn_max = self._chrome_button(chrome, "□", hover=CARD2, command=lambda: self._toggle_max())
        self.btn_min = self._chrome_button(chrome, "–", hover=CARD2, command=lambda: self.root.iconify())

    def _chrome_button(self, parent, text, hover, command) -> tk.Label:
        b = tk.Label(parent, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 10),
                     width=4, cursor="hand2")
        b.pack(side="right")
        b.bind("<Button-1>", lambda _e: command())
        b.bind("<Enter>", lambda _e: b.configure(bg=hover, fg="#ffffff" if hover == "#e81123" else FG))
        b.bind("<Leave>", lambda _e: b.configure(bg=PANEL, fg=MUTED))
        return b

    def _drag_start(self, e) -> None:
        if self._maxed:
            return
        self._drag_off = (e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty())

    def _drag_move(self, e) -> None:
        if self._maxed or self._drag_off is None:
            return
        self.root.geometry(f"+{e.x_root - self._drag_off[0]}+{e.y_root - self._drag_off[1]}")

    def _work_area(self) -> tuple[int, int, int, int]:
        """Work area layar (tanpa taskbar) via SystemParametersInfo; fallback layar penuh."""
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            if rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return 0, 0, sw, max(0, sh - 48)

    def _toggle_max(self, _e=None) -> None:
        if self._maxed:
            self.root.geometry(self._normal_geo)
            self._maxed = False
            self.btn_max.configure(text="□")
        else:
            self._normal_geo = self.root.geometry()
            ax, ay, aw, ah = self._work_area()
            self.root.geometry(f"{aw}x{ah}+{ax}+{ay}")
            self._maxed = True
            self.btn_max.configure(text="❐")

    def _build_edges(self) -> None:
        """Bingkai resize tipis di tepi jendela (frameless window)."""
        specs = [
            ("s", "sb_v_double_arrow", PANEL, dict(x=0, rely=1, y=-6, relwidth=1, height=6)),
            ("n", "sb_v_double_arrow", PANEL, dict(x=170, y=0, relwidth=1, width=-320, height=5)),
            ("w", "sb_h_double_arrow", BG, dict(x=0, y=46, relheight=1, height=-14, width=5)),
            ("e", "sb_h_double_arrow", BG, dict(relx=1, x=-5, y=46, relheight=1, height=-14, width=5)),
        ]
        for dirs, cur, bgc, placekw in specs:
            f = tk.Frame(self.main, bg=bgc, cursor=cur)
            f.place(**placekw)
            f.bind("<ButtonPress-1>", lambda e, d=dirs: self._rs_start(e, d))
            f.bind("<B1-Motion>", self._rs_move)

    def _rs_start(self, e, dirs: str) -> None:
        if self._maxed:
            return
        self._rs = (dirs, e.x_root, e.y_root,
                    self.root.winfo_width(), self.root.winfo_height(),
                    self.root.winfo_rootx(), self.root.winfo_rooty())

    def _rs_move(self, e) -> None:
        if self._rs is None or self._maxed:
            return
        dirs, sx, sy, w0, h0, x0, y0 = self._rs
        dw, dh = e.x_root - sx, e.y_root - sy
        w, h, x, y = w0, h0, x0, y0
        if "e" in dirs:
            w = max(1180, w0 + dw)
        if "s" in dirs:
            h = max(720, h0 + dh)
        if "w" in dirs:
            w = max(1180, w0 - dw)
            x = x0 + (w0 - w)
        if "n" in dirs:
            h = max(720, h0 - dh)
            y = y0 + (h0 - h)
        self._normal_geo = f"{w}x{h}+{x}+{y}"
        self.root.geometry(self._normal_geo)

    # ------------------------------------------------------------- layout
    def _card(self, parent, padding: int = 10):
        """Kartu dengan border 1px: kembalikan (outer_frame, inner_card)."""
        outer = tk.Frame(parent, bg=BORDER, bd=0, highlightthickness=0)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=padding)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _build_layout(self) -> None:
        content = ttk.Frame(self.main, padding=(14, 10, 14, 8))
        content.pack(fill="both", expand=True)

        # --- header: brand + kontrol profil ---
        hdr_outer, hdr = self._card(content, padding=(12, 9))
        hdr_outer.pack(fill="x", pady=(0, 10))
        left = ttk.Frame(hdr, style="Card.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="SahamBot Desktop", style="Card.Title.TLabel").pack(anchor="w")
        ttk.Label(left, text=dm.DELAY_DISCLOSURE, style="Card.Muted.TLabel").pack(anchor="w")

        ctrl = ttk.Frame(hdr, style="Card.TFrame")
        ctrl.pack(side="right")
        ttk.Label(ctrl, text="Profil:", style="Card.TLabel").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(ctrl, textvariable=self.profile_var, width=9).grid(row=0, column=1, padx=(0, 8))
        ttk.Label(ctrl, text="Modal (Rp):", style="Card.TLabel").grid(row=0, column=2, padx=(0, 4))
        ttk.Entry(ctrl, textvariable=self.modal_var, width=11).grid(row=0, column=3, padx=(0, 8))
        ttk.Label(ctrl, text="Interval:", style="Card.TLabel").grid(row=0, column=4, padx=(0, 4))
        ttk.Combobox(ctrl, textvariable=self.interval_var, values=("30", "60", "120", "300"),
                     width=5, state="readonly").grid(row=0, column=5, padx=(0, 8))

        auto_fr = ttk.Frame(ctrl, style="Card.TFrame")
        ttk.Label(auto_fr, text="Auto-Pantau", style="Card.TLabel").pack(side="left", padx=(0, 5))
        Switch(auto_fr, self.auto_var, command=self._reschedule_timer, bg=CARD).pack(side="left")
        auto_fr.grid(row=0, column=6, padx=(0, 8))

        toast_fr = ttk.Frame(ctrl, style="Card.TFrame")
        ttk.Label(toast_fr, text="Toast", style="Card.TLabel").pack(side="left", padx=(0, 5))
        Switch(toast_fr, self.toast_var, bg=CARD).pack(side="left")
        toast_fr.grid(row=0, column=7, padx=(0, 10))

        tk.Frame(ctrl, bg=BORDER, width=1).grid(row=0, column=8, sticky="ns", padx=(6, 12))
        ttk.Button(ctrl, text="Terapkan", command=self._save_profile).grid(row=0, column=9, padx=(0, 6))
        ttk.Button(ctrl, text="⟳  Refresh", style="Accent.TButton",
                   command=self.trigger_refresh_all).grid(row=0, column=10, padx=(0, 6))
        ttk.Button(ctrl, text="Keluar", style="Danger.TButton", command=self._quit_app).grid(row=0, column=11)

        # --- strip KPI ---
        kpi_row = ttk.Frame(content)
        kpi_row.pack(fill="x", pady=(0, 10))
        for i, wgt in enumerate((3, 2, 3, 2)):
            kpi_row.grid_columnconfigure(i, weight=wgt)

        ihsg_card, ihsg_top = self._kpi_card(kpi_row, 0, "IHSG", BLUE)
        self.ihsg_chip = ttk.Label(ihsg_top, textvariable=self.ihsg_chg_var, style="Chip.gray.TLabel")
        self.ihsg_chip.pack(side="right")
        self.ihsg_val = self._kpi_value(ihsg_card, self.ihsg_close_var, FG, 16)

        regime_card, _regime_top = self._kpi_card(kpi_row, 1, "REZIM IHSG", AMBER)
        self.regime_val = self._kpi_value(regime_card, self.regime_var, AMBER, 14)
        ttk.Label(regime_card, textvariable=self.regime_sub_var, style="Card.Muted.TLabel").pack(anchor="w")

        cond_card, _cond_top = self._kpi_card(kpi_row, 2, "KONDISI PASAR", AMBER)
        self.cond_val = self._kpi_value(cond_card, self.cond_var, AMBER, 14)
        ttk.Label(cond_card, textvariable=self.cond_sub_var, style="Card.Muted.TLabel",
                  wraplength=380).pack(anchor="w")

        upd_card, _upd_top = self._kpi_card(kpi_row, 3, "UPDATE TERAKHIR", GREEN)
        self._kpi_value(upd_card, self.update_var, FG, 14)
        self.auto_chip = ttk.Label(upd_card, textvariable=self.auto_chip_var, style="Chip.green.TLabel")
        self.auto_chip.pack(anchor="w", pady=(2, 0))

        # --- tab bar segmented + host ---
        bar = tk.Frame(content, bg=PANEL, height=38)
        bar.pack(fill="x", pady=(0, 0))
        bar.pack_propagate(False)

        self.tab_host = ttk.Frame(content)
        self.tab_host.pack(fill="both", expand=True, pady=(8, 0))

        self.tab_frames: dict[str, ttk.Frame] = {}
        self.tab_buttons: dict[str, tk.Label] = {}
        tab_defs = [
            ("market", "🌐 Kondisi Pasar"),
            ("watch", "📡 Radar Watchlist"),
            ("sinyal", "📊 Sinyal & Scan BEI"),
            ("paket", "🎯 Paket Beli"),
            ("port", "💼 Portofolio & Tesis"),
            ("jurnal", "📒 Jurnal & Alpha"),
            ("cek", "🔍 Cek Emiten"),
            ("alerts", "🔔 Log Alert"),
        ]
        for key, label in tab_defs:
            frame = ttk.Frame(self.tab_host)
            self.tab_frames[key] = frame
            b = tk.Label(bar, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"),
                         padx=12, cursor="hand2")
            b.pack(side="left", padx=(3, 3), pady=5)
            b.bind("<Button-1>", lambda _e, k=key: self._select_tab(k))
            b.bind("<Enter>", lambda _e, b=b, k=key: self._tab_hover(b, k, True))
            b.bind("<Leave>", lambda _e, b=b, k=key: self._tab_hover(b, k, False))
            self.tab_buttons[key] = b
        ttk.Label(bar, text="8 tab · klik ganda baris tabel untuk cek detail emiten",
                  style="Panel.Muted.TLabel").pack(side="right", padx=10)
        tk.Frame(content, bg=BORDER, height=1).pack(fill="x")
        self._select_tab("market")

        self.tab_market = self.tab_frames["market"]
        self.tab_watch = self.tab_frames["watch"]
        self.tab_sinyal = self.tab_frames["sinyal"]
        self.tab_paket = self.tab_frames["paket"]
        self.tab_port = self.tab_frames["port"]
        self.tab_jurnal = self.tab_frames["jurnal"]
        self.tab_cek = self.tab_frames["cek"]
        self.tab_alerts = self.tab_frames["alerts"]

        self._build_market_tab()
        self._build_watch_tab()
        self._build_sinyal_tab()
        self._build_paket_tab()
        self._build_portfolio_tab()
        self._build_jurnal_tab()
        self._build_cek_tab()
        self._build_alerts_tab()

        # --- status bar ---
        tk.Frame(self.main, bg=BORDER, height=1).pack(fill="x")
        sbar = tk.Frame(self.main, bg=PANEL, height=30)
        sbar.pack(fill="x")
        sbar.pack_propagate(False)
        self.status_dot = tk.Label(sbar, text="●", bg=PANEL, fg=GREEN, font=("Segoe UI", 9))
        self.status_dot.pack(side="left", padx=(12, 6))
        ttk.Label(sbar, textvariable=self.status_var, style="Panel.TLabel").pack(side="left")
        self.auto_chip_bar = ttk.Label(sbar, textvariable=self.auto_chip_var, style="Chip.green.TLabel")
        self.auto_chip_bar.pack(side="right", padx=12)

    def _kpi_card(self, parent, col: int, title: str, accent: str):
        """Satu kartu KPI: strip aksen kiri + label kecil. Mengembalikan (body, top_row)."""
        outer, body = self._card(parent, padding=0)
        outer.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
        strip = tk.Frame(body, bg=accent, width=3)
        strip.pack(side="left", fill="y")
        inner = ttk.Frame(body, style="Card.TFrame", padding=(10, 8))
        inner.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(inner, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=title, style="KpiLabel.TLabel").pack(side="left")
        return inner, top

    def _kpi_value(self, parent, var: tk.StringVar, fg: str, size: int) -> tk.Label:
        v = tk.Label(parent, textvariable=var, bg=CARD, fg=fg, font=("Segoe UI", size, "bold"))
        v.pack(anchor="w", pady=(1, 0))
        return v

    def _select_tab(self, key: str) -> None:
        for f in self.tab_frames.values():
            f.pack_forget()
        for k, b in self.tab_buttons.items():
            if k == key:
                b.configure(bg=ACCENT_D, fg="#ffffff")
            else:
                b.configure(bg=PANEL, fg=MUTED)
        self.tab_frames[key].pack(fill="both", expand=True)

    def _tab_hover(self, b: tk.Label, key: str, enter: bool) -> None:
        if str(b.cget("bg")) == ACCENT_D:
            return
        if enter:
            b.configure(bg=CARD2, fg=FG)
        else:
            b.configure(bg=PANEL, fg=MUTED)

    def _setup_rich_tags(self, tw: tk.Text) -> None:
        tw.tag_configure("h", foreground=BLUE, font=("Consolas", 10, "bold"))
        tw.tag_configure("sec", foreground=MUTED, font=("Consolas", 10, "bold"))
        tw.tag_configure("muted", foreground=MUTED)
        tw.tag_configure("green", foreground=GREEN)
        tw.tag_configure("red", foreground=RED)
        tw.tag_configure("amber", foreground=AMBER)
        tw.tag_configure("bold", font=("Consolas", 10, "bold"))

    def _rich_lines(self, tw: tk.Text, lines: list[str]) -> None:
        """Render teks laporan dengan pewarnaan: header, bullet, & kata kunci status."""
        self._setup_rich_tags(tw)
        tw.configure(state="normal")
        tw.delete("1.0", tk.END)
        kw_tags = (
            ("VERDICT", "bold"), ("LAYAK", "green"), ("MENGUAT", "green"), ("BELI", "green"),
            ("POSITIF", "green"), ("BULL", "green"),
            ("GUGUR", "red"), ("HINDARI", "red"), ("JUAL", "red"), ("NEGATIF", "red"), ("BEAR", "red"),
            ("TUNGGU", "amber"), ("MELEMAH", "amber"), ("WASPADA", "amber"), ("DEFENSIVE", "amber"), ("CAMPURAN", "amber"),
        )
        for ln in lines:
            s = ln.strip()
            tags: list[str] = []
            if s.startswith("==="):
                tags.append("h")
            elif s.startswith("---"):
                tags.append("sec")
            elif s.startswith("•") or s.startswith("+ "):
                tags.append("muted")
            upper = s.upper()
            for kw, tag in kw_tags:
                if kw in upper and tag not in tags:
                    tags.append(tag)
            tw.insert(tk.END, ln + "\n", tags or None)
        tw.configure(state="disabled")

    def _tree_base(self, parent, cols, headers, tags_fg: dict[str, str]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=cols, show="headings")
        for c, (label, width) in headers.items():
            tree.heading(c, text=label)
            tree.column(c, width=width)
        tree.tag_configure("odd", background="#15203a")
        for tag, color in tags_fg.items():
            tree.tag_configure(tag, foreground=color)
        return tree

    def _empty_label(self, parent, text: str) -> ttk.Label:
        """Label empty-state yang melayang di tengah area konten."""
        lbl = ttk.Label(parent, text=text, style="Empty.TLabel", anchor="center", justify="center", wraplength=460)
        lbl.place(relx=0.5, rely=0.42, anchor="center")
        return lbl

    @staticmethod
    def _show_empty(lbl: ttk.Label) -> None:
        lbl.place(relx=0.5, rely=0.42, anchor="center")
        lbl.lift()

    @staticmethod
    def _hide_empty(lbl: ttk.Label) -> None:
        lbl.place_forget()

    def _chip_row(self, parent, counts: dict[str, int], order: tuple[str, ...]) -> None:
        """Render baris chip kecil (mis. 'BELI 3') di dalam parent (hancurkan isi lama)."""
        for w in parent.winfo_children():
            w.destroy()
        for key in order:
            n = counts.get(key, 0)
            if n:
                ttk.Label(parent, text=f"{key} {n}", style=f"Chip.{_chip_for(key)}.TLabel").pack(side="left", padx=(0, 6))

    # ------------------------------------------------------------- tabs
    def _build_market_tab(self) -> None:
        card, body = self._card(self.tab_market, padding=0)
        card.pack(fill="both", expand=True)
        pane = tk.PanedWindow(body, orient="horizontal", bg=BORDER, sashwidth=5,
                              sashrelief="flat", bd=0)
        pane.pack(fill="both", expand=True)

        # kiri: laporan teks
        left_outer = tk.Frame(pane, bg=BORDER)
        left = ttk.Frame(left_outer, style="Card.TFrame")
        left.pack(fill="both", expand=True, padx=1, pady=1)
        self.market_text = tk.Text(
            left, wrap="word", bg=TEXTBG, fg=FG, insertbackground=FG,
            font=("Consolas", 10), relief="flat", padx=14, pady=12, state="disabled",
        )
        sb = ttk.Scrollbar(left, orient="vertical", command=self.market_text.yview)
        self.market_text.configure(yscrollcommand=sb.set)
        self.market_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pane.add(left_outer, stretch="always", minsize=430)

        # kanan: ringkasan visual (breadth, penggerak, sentimen berita)
        right_outer = tk.Frame(pane, bg=BORDER)
        right = ttk.Frame(right_outer, style="Card.TFrame", padding=12)
        right.pack(fill="both", expand=True, padx=1, pady=1)
        pane.add(right_outer, stretch="never", width=390, minsize=330)

        ttk.Label(right, text="BREADTH / HASIL SCAN", style="KpiLabel.TLabel").pack(anchor="w")
        self.breadth_canvas = tk.Canvas(right, height=16, bg=TEXTBG, highlightthickness=1,
                                        highlightbackground=BORDER)
        self.breadth_canvas.pack(fill="x", pady=(6, 4))
        self.breadth_counts_var = tk.StringVar(value="—")
        ttk.Label(right, textvariable=self.breadth_counts_var, style="Card.TLabel").pack(anchor="w")
        self.breadth_ma_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.breadth_ma_var, style="Card.Muted.TLabel").pack(anchor="w")
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=10)

        ttk.Label(right, text="PENGGERAK NAIK / TURUN", style="KpiLabel.TLabel").pack(anchor="w")
        self.market_gainers = tk.Label(right, text="—", bg=CARD, fg=GREEN, font=("Segoe UI", 9),
                                       justify="left", wraplength=340, anchor="nw")
        self.market_gainers.pack(anchor="w", pady=(4, 2))
        self.market_losers = tk.Label(right, text="—", bg=CARD, fg=RED, font=("Segoe UI", 9),
                                      justify="left", wraplength=340, anchor="nw")
        self.market_losers.pack(anchor="w")
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=10)

        sent_top = ttk.Frame(right, style="Card.TFrame")
        sent_top.pack(fill="x")
        ttk.Label(sent_top, text="SENTIMEN BERITA PASAR", style="KpiLabel.TLabel").pack(side="left")
        self.news_chip = ttk.Label(sent_top, text="—", style="Chip.gray.TLabel")
        self.news_chip.pack(side="right")
        self.news_score_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.news_score_var, style="Card.Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.news_headlines = tk.Label(right, text="—", bg=CARD, fg=MUTED, font=("Segoe UI", 8),
                                       justify="left", wraplength=340, anchor="nw")
        self.news_headlines.pack(anchor="w", pady=(4, 0))

    def _build_watch_tab(self) -> None:
        card, body = self._card(self.tab_watch, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Tambah Kode:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.add_watch_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.add_watch_var, width=12)
        ent.pack(side="left", padx=(0, 6))
        ent.bind("<Return>", lambda _e: self._add_watch())
        ttk.Button(bar, text="+ Tambah Watchlist", command=self._add_watch).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Hapus Terpilih", style="Danger.TButton", command=self._remove_watch).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Cek Detail Terpilih", command=self._inspect_selected_watch).pack(side="left")
        ttk.Label(bar, text="Klik ganda baris untuk cek detail & grafik", style="Card.Muted.TLabel").pack(side="right")

        cols = ("ticker", "price", "ret1d", "action", "score", "entry", "sl", "tp", "lots", "reason")
        headers = {
            "ticker": ("Kode", 75), "price": ("Harga", 90), "ret1d": ("%1H", 75),
            "action": ("Saran", 95), "score": ("Skor", 65), "entry": ("Entry", 85),
            "sl": ("SL", 85), "tp": ("TP", 85), "lots": ("Lot", 55), "reason": ("Alasan", 360),
        }
        self.watch_tree = self._tree_base(body, cols, headers, {
            "BELI": GREEN, "SIAP BELI": BLUE, "JUAL": RED, "TAHAN": FG, "TIDAK TERSEDIA": MUTED,
        })
        for c in cols:
            self.watch_tree.column(c, anchor="w" if c in ("ticker", "action", "reason") else "e")
        self.watch_tree.bind("<Double-1>", lambda _e: self._inspect_selected_watch())
        self.watch_tree.pack(fill="both", expand=True)
        self.watch_empty = self._empty_label(
            body, "Watchlist kosong — tambahkan kode emiten di atas (mis. BBCA), lalu tekan Refresh.")
        foot = ttk.Frame(body, style="Card.TFrame")
        foot.pack(fill="x", pady=(8, 0))
        self.watch_foot_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.watch_foot_var, style="Card.Muted.TLabel").pack(side="left")
        self.watch_chips = ttk.Frame(foot, style="Card.TFrame")
        self.watch_chips.pack(side="right")

    def _build_sinyal_tab(self) -> None:
        card, body = self._card(self.tab_sinyal, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Filter Sinyal:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(bar, textvariable=self.sinyal_filter_var, values=("ALL", "BELI", "JUAL", "TAHAN"),
                          width=8, state="readonly")
        cb.pack(side="left", padx=(0, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self.trigger_load_sinyal())
        ttk.Checkbutton(bar, text="BELI Berkualitas (Fundamental Sehat)", variable=self.sinyal_quality_var,
                        command=self.trigger_load_sinyal).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Muat Snapshot", command=self.trigger_load_sinyal).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="⚡ Scan 944 Emiten BEI (/scan)", style="Accent.TButton",
                   command=self.trigger_full_scan).pack(side="left")
        ttk.Label(bar, text="Hasil scan & ringkasan ada di footer bawah tabel", style="Card.Muted.TLabel").pack(side="right")

        cols = ("ticker", "name", "price", "ret1d", "signal", "rel_pct", "entry", "sl", "tp", "quality", "reason")
        headers = {
            "ticker": ("Kode", 65), "name": ("Emiten", 150), "price": ("Harga", 85),
            "ret1d": ("%1H", 65), "signal": ("Sinyal", 70), "rel_pct": ("Rank%", 65),
            "entry": ("Entry", 80), "sl": ("SL", 80), "tp": ("TP", 80),
            "quality": ("Fund", 50), "reason": ("Catatan", 290),
        }
        self.sinyal_tree = self._tree_base(body, cols, headers, {"BELI": GREEN, "JUAL": RED, "TAHAN": FG})
        for c in cols:
            self.sinyal_tree.column(c, anchor="w" if c in ("ticker", "name", "signal", "quality", "reason") else "e")
        self.sinyal_tree.bind("<Double-1>", lambda _e: self._inspect_selected_tree(self.sinyal_tree))
        self.sinyal_tree.pack(fill="both", expand=True)
        self.sinyal_empty = self._empty_label(
            body, "Belum ada snapshot sinyal — klik 'Muat Snapshot' atau jalankan '⚡ Scan 944 Emiten BEI'.")
        foot = ttk.Frame(body, style="Card.TFrame")
        foot.pack(fill="x", pady=(8, 0))
        self.sinyal_meta_var = tk.StringVar(value="Snapshot: -")
        ttk.Label(foot, textvariable=self.sinyal_meta_var, style="Card.Muted.TLabel").pack(side="left")
        self.sinyal_chips = ttk.Frame(foot, style="Card.TFrame")
        self.sinyal_chips.pack(side="right")

    def _build_paket_tab(self) -> None:
        card, body = self._card(self.tab_paket, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="🎯 Susun Paket Beli Stockbit (/paket)", style="Accent.TButton",
                   command=self.trigger_build_paket).pack(side="left", padx=(0, 8))
        self.paket_info_var = tk.StringVar(value="Klik tombol di kiri untuk menyusun paket multi-saham dari snapshot /scan.")
        ttk.Label(bar, textvariable=self.paket_info_var, style="Card.Muted.TLabel").pack(side="left")

        cols = ("ticker", "grade", "entry", "sl", "tp", "lots", "value", "risk", "rr", "notes")
        headers = {
            "ticker": ("Kode", 70), "grade": ("Grade", 60), "entry": ("Entry Limit", 90),
            "sl": ("SL", 85), "tp": ("TP", 85), "lots": ("Lot", 55),
            "value": ("Nilai Beli", 110), "risk": ("Risiko Maks", 95), "rr": ("R:R", 55),
            "notes": ("Catatan / Peringatan", 360),
        }
        self.paket_tree = self._tree_base(body, cols, headers, {})
        for c in cols:
            self.paket_tree.column(c, anchor="w" if c in ("ticker", "grade", "notes") else "e")
        self.paket_tree.bind("<Double-1>", lambda _e: self._inspect_selected_tree(self.paket_tree))
        self.paket_tree.pack(fill="both", expand=True)
        self.paket_empty = self._empty_label(
            body, "Belum ada paket beli — jalankan '⚡ Scan 944 Emiten BEI' di tab Sinyal & Scan BEI,\nlalu klik '🎯 Susun Paket Beli Stockbit'.")

    def _build_portfolio_tab(self) -> None:
        card, body = self._card(self.tab_port, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        self.tx_ticker = tk.StringVar()
        self.tx_lots = tk.StringVar()
        self.tx_price = tk.StringVar()
        ttk.Label(bar, text="Kode:", style="Card.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(bar, textvariable=self.tx_ticker, width=9).pack(side="left", padx=(0, 6))
        ttk.Label(bar, text="Lot:", style="Card.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(bar, textvariable=self.tx_lots, width=6).pack(side="left", padx=(0, 6))
        ttk.Label(bar, text="Harga:", style="Card.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(bar, textvariable=self.tx_price, width=9).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Catat Beli", style="Accent.TButton", command=lambda: self._record_tx("buy")).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Catat Jual", style="Danger.TButton", command=lambda: self._record_tx("sell")).pack(side="left")
        ttk.Label(bar, text="Catat manual sesuai eksekusi Stockbit PC — tanpa auto-trading & tanpa order otomatis",
                  style="Card.Muted.TLabel").pack(side="right")

        cols = ("ticker", "lots", "avg", "price", "pnl", "sl", "trail", "tp", "thesis", "reasons")
        headers = {
            "ticker": ("Kode", 75), "lots": ("Lot", 55), "avg": ("Avg Beli", 90),
            "price": ("Harga", 90), "pnl": ("PnL Net", 80), "sl": ("SL Efektif", 90),
            "trail": ("Trailing 22h", 95), "tp": ("TP", 85), "thesis": ("Tesis", 105),
            "reasons": ("Alasan Tesis", 340),
        }
        self.port_tree = self._tree_base(body, cols, headers, {
            "GUGUR": RED, "MELEMAH": AMBER, "MENGUAT": GREEN, "TETAP": FG,
        })
        for c in cols:
            self.port_tree.column(c, anchor="w" if c in ("ticker", "thesis", "reasons") else "e")
        self.port_tree.pack(fill="both", expand=True)
        self.port_empty = self._empty_label(
            body, "Belum ada posisi terbuka — catat eksekusi Beli/Jual manual di atas\nsesuai order yang kamu eksekusi di Stockbit.")
        foot = ttk.Frame(body, style="Card.TFrame")
        foot.pack(fill="x", pady=(8, 0))
        self.port_foot_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.port_foot_var, style="Card.Muted.TLabel").pack(side="left")

    def _build_jurnal_tab(self) -> None:
        card, body = self._card(self.tab_jurnal, padding=0)
        card.pack(fill="both", expand=True)
        ttk.Button(body, text="Muat Ulang Jurnal (/jurnal)", command=self.trigger_load_jurnal).pack(anchor="w", padx=12, pady=(10, 6))
        text_fr = ttk.Frame(body, style="Card.TFrame")
        text_fr.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.jurnal_text = tk.Text(
            text_fr, wrap="word", bg=TEXTBG, fg=FG, insertbackground=FG,
            font=("Consolas", 10), relief="flat", padx=14, pady=12, state="disabled",
        )
        sb = ttk.Scrollbar(text_fr, orient="vertical", command=self.jurnal_text.yview)
        self.jurnal_text.configure(yscrollcommand=sb.set)
        self.jurnal_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _build_cek_tab(self) -> None:
        card, body = self._card(self.tab_cek, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Kode Saham BEI:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.cek_ticker_var = tk.StringVar(value="BBCA")
        ent = ttk.Entry(bar, textvariable=self.cek_ticker_var, width=12)
        ent.pack(side="left", padx=(0, 6))
        ent.bind("<Return>", lambda _e: self.trigger_inspect_ticker())
        ttk.Button(bar, text="Analisis & Cek BUY/SELL", style="Accent.TButton",
                   command=self.trigger_inspect_ticker).pack(side="left")

        pane = tk.PanedWindow(body, orient="horizontal", bg=BORDER, sashwidth=5,
                              sashrelief="flat", bd=0, opaqueresize=False)
        pane.pack(fill="both", expand=True)

        self.cek_text = tk.Text(
            pane, wrap="word", bg=TEXTBG, fg=FG, insertbackground=FG,
            font=("Consolas", 10), relief="flat", padx=14, pady=12, state="disabled", width=56,
        )
        pane.add(self.cek_text, stretch="always", minsize=380)

        chart_outer = tk.Frame(pane, bg=BORDER)
        chart_inner = ttk.Frame(chart_outer, style="Card.TFrame")
        chart_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.chart_label = ttk.Label(chart_inner, text="Grafik akan tampil setelah Cek Emiten dijalankan.",
                                     style="Card.Muted.TLabel", anchor="center", wraplength=460, justify="center")
        self.chart_label.pack(fill="both", expand=True, padx=10, pady=10)
        pane.add(chart_outer, stretch="always", minsize=420)

    def _build_alerts_tab(self) -> None:
        card, body = self._card(self.tab_alerts, padding=10)
        card.pack(fill="both", expand=True)
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Riwayat alert BUY / SELL / Perubahan Tesis (tidak berulang selama status sama):",
                  style="Card.TLabel").pack(side="left")
        ttk.Button(bar, text="Bersihkan Log", command=self._clear_alerts).pack(side="right")

        self.alert_list = tk.Text(
            body, wrap="word", bg=TEXTBG, fg=FG, insertbackground=FG,
            font=("Consolas", 9), relief="flat", padx=12, pady=10, state="disabled",
        )
        self.alert_empty = self._empty_label(
            body, "Belum ada alert — alert muncul saat sinyal watchlist berubah (BELI/JUAL)\natau status tesis posisi berubah (GUGUR/MELEMAH/MENGUAT).")
        for tag, color in (("green", GREEN), ("red", RED), ("amber", AMBER), ("gray", MUTED), ("blue", BLUE)):
            self.alert_list.tag_configure(tag, foreground=color)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.alert_list.yview)
        self.alert_list.configure(yscrollcommand=sb.set)
        self.alert_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------- tray & window
    def _setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Buka SahamBot", lambda _icon, _item: self.ui_queue.put(("tray_show", None)), default=True),
            pystray.MenuItem("Refresh Sekarang", lambda _icon, _item: self.ui_queue.put(("tray_refresh", None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Keluar", lambda _icon, _item: self.ui_queue.put(("tray_quit", None))),
        )
        self._tray_icon = pystray.Icon("SahamBot", create_tray_icon_image(), "SahamBot Desktop", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _minimize_to_tray(self) -> None:
        self.root.withdraw()
        if not self._tray_notified:
            self._tray_notified = True
            if self.toast_var.get():
                send_windows_toast(
                    "SahamBot berjalan di background",
                    "Klik ikon SahamBot di System Tray untuk membuka kembali atau keluar.",
                )

    def _show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_app(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    # ------------------------------------------------------------- aksi kecil
    def _get_profile_and_modal(self) -> tuple[str, int]:
        cid = self.profile_var.get().strip() or dm.DEFAULT_PROFILE_ID
        try:
            modal = textutil.parse_rupiah(self.modal_var.get()) or 10_000_000
        except Exception:
            modal = 10_000_000
        return cid, max(0, int(modal))

    def _load_initial_profile(self) -> None:
        cid, _ = self._get_profile_and_modal()
        saved_modal = store_watchlist.get_modal(cid)
        if saved_modal and saved_modal > 0:
            self.modal_var.set(str(saved_modal))
        if not store_watchlist.get_watchlist(cid):
            for default_t in ("BBCA", "BBRI", "BMRI", "TLKM"):
                store_watchlist.watchlist_add(cid, default_t)

    def _save_profile(self) -> None:
        cid, modal = self._get_profile_and_modal()
        store_watchlist.set_modal(cid, modal)
        self.status_var.set(f"Profil '{cid}' & modal {_fmt_rp(modal)} disimpan.")
        self.trigger_refresh_all()

    def _add_watch(self) -> None:
        sym = self.add_watch_var.get().strip().upper().removesuffix(".JK")
        if not sym:
            return
        cid, _ = self._get_profile_and_modal()
        ok, _ = store_watchlist.watchlist_add(cid, sym)
        self.add_watch_var.set("")
        if ok:
            self.status_var.set(f"{sym} ditambahkan ke watchlist '{cid}'.")
            self.trigger_refresh_all()
        else:
            messagebox.showinfo("Watchlist", f"{sym} sudah ada atau batas watchlist penuh.")

    def _remove_watch(self) -> None:
        sel = self.watch_tree.selection()
        if not sel:
            return
        sym = self.watch_tree.item(sel[0], "values")[0]
        cid, _ = self._get_profile_and_modal()
        store_watchlist.watchlist_remove(cid, sym)
        self.trigger_refresh_all()

    def _inspect_selected_tree(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if not sel:
            return
        sym = tree.item(sel[0], "values")[0]
        self.cek_ticker_var.set(sym)
        self._select_tab("cek")
        self.trigger_inspect_ticker()

    def _inspect_selected_watch(self) -> None:
        self._inspect_selected_tree(self.watch_tree)

    def _record_tx(self, side: str) -> None:
        sym = self.tx_ticker.get().strip().upper().removesuffix(".JK")
        try:
            lots = int(self.tx_lots.get().strip())
            px = float(self.tx_price.get().strip())
        except ValueError:
            messagebox.showerror("Input Tidak Valid", "Kode, lot (angka bulat), dan harga wajib diisi.")
            return
        if not sym or lots <= 0 or px <= 0:
            messagebox.showerror("Input Tidak Valid", "Lot dan harga harus > 0.")
            return
        cid, _ = self._get_profile_and_modal()
        trade_date = dm.start_of_day_tag()
        try:
            if side == "buy":
                store_portfolio.record_execution_buy(cid, sym, lots, px, trade_date=trade_date, source="desktop")
                self.status_var.set(f"Catat BELI {sym} {lots} lot @ {_fmt_rp(px)}")
            else:
                store_portfolio.record_execution_sell(cid, sym, lots, px, trade_date=trade_date, source="desktop")
                self.status_var.set(f"Catat JUAL {sym} {lots} lot @ {_fmt_rp(px)}")
        except Exception as exc:
            messagebox.showerror("Gagal Mencatat", str(exc))
            return
        self.tx_ticker.set("")
        self.tx_lots.set("")
        self.tx_price.set("")
        self.trigger_refresh_all()

    def _alert_insert(self, al: dict) -> None:
        line = f"[{al['time']}] [{al['kind']}] {al['title']} — {al['message']}\n"
        kind = (al.get("kind") or "").upper()
        tag = "green" if kind == "BELI" else "red" if kind == "JUAL" else "amber" if kind else "gray"
        self.alert_list.configure(state="normal")
        self.alert_list.insert("1.0", line, tag)
        self.alert_list.configure(state="disabled")
        self._hide_empty(self.alert_empty)

    def _clear_alerts(self) -> None:
        self.alert_list.configure(state="normal")
        self.alert_list.delete("1.0", tk.END)
        self.alert_list.configure(state="disabled")
        self._show_empty(self.alert_empty)

    def _reschedule_timer(self) -> None:
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        on = self.auto_var.get()
        try:
            sec = max(15, int(self.interval_var.get()))
        except ValueError:
            sec = 60
        self.auto_chip_var.set(f"AUTO-PANTAU {'ON' if on else 'OFF'} · {sec} DTK")
        chip = "green" if on else "gray"
        self.auto_chip.configure(style=f"Chip.{chip}.TLabel")
        self.auto_chip_bar.configure(style=f"Chip.{chip}.TLabel")
        self.status_dot.configure(fg=GREEN if on else MUTED)
        if on:
            self._after_id = self.root.after(sec * 1000, self.trigger_refresh_all)

    # ------------------------------------------------------------- worker triggers
    def trigger_refresh_all(self) -> None:
        if self._refresh_busy:
            self._reschedule_timer()
            return
        self._refresh_busy = True
        self.status_var.set("Memperbarui data pasar, watchlist, portofolio, & jurnal...")
        cid, modal = self._get_profile_and_modal()
        sig_f = self.sinyal_filter_var.get()
        q_only = self.sinyal_quality_var.get()

        def _worker():
            try:
                pulse = dm.fetch_market_pulse(include_idx=True, include_news=True)
                reg = pulse.get("ihsg", {}).get("regime")
                tickers = store_watchlist.get_watchlist(cid)
                watch_rows = dm.evaluate_watchlist_rows(tickers, modal, market_regime=reg)
                port_rows = dm.evaluate_portfolio_rows(cid, market_regime=reg)
                sinyal_data = dm.load_sinyal_rows(sig_f, quality_only=q_only)
                jurnal_data = dm.build_journal_summary(cid)
                self.ui_queue.put(("refresh_ok", {
                    "pulse": pulse,
                    "watch_rows": watch_rows,
                    "port_rows": port_rows,
                    "sinyal_data": sinyal_data,
                    "jurnal_data": jurnal_data,
                }))
            except Exception as exc:
                self.ui_queue.put(("refresh_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()
        self._reschedule_timer()

    def trigger_load_sinyal(self) -> None:
        sig_f = self.sinyal_filter_var.get()
        q_only = self.sinyal_quality_var.get()
        self.status_var.set("Memuat snapshot sinyal BEI...")

        def _worker():
            try:
                data_s = dm.load_sinyal_rows(sig_f, quality_only=q_only)
                self.ui_queue.put(("sinyal_ok", data_s))
            except Exception as exc:
                self.ui_queue.put(("refresh_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_full_scan(self) -> None:
        if self._scan_busy:
            messagebox.showinfo("Scan BEI", "Scan penuh 944 emiten sedang berjalan di background.")
            return
        self._scan_busy = True
        self.status_var.set("Menjalankan scan penuh ~944 emiten BEI (±2-5 menit)...")

        def _worker():
            try:
                res = dm.run_full_market_scan(force=True)
                self.ui_queue.put(("scan_ok", res))
            except Exception as exc:
                self.ui_queue.put(("scan_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_build_paket(self) -> None:
        cid, modal = self._get_profile_and_modal()
        self.status_var.set("Menyusun paket beli Stockbit...")

        def _worker():
            try:
                res = dm.build_paket_picks(modal, chat_id=cid, market_regime=self._last_regime)
                self.ui_queue.put(("paket_ok", res))
            except Exception as exc:
                self.ui_queue.put(("refresh_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_load_jurnal(self) -> None:
        cid, _ = self._get_profile_and_modal()

        def _worker():
            try:
                res = dm.build_journal_summary(cid)
                self.ui_queue.put(("jurnal_ok", res))
            except Exception as exc:
                self.ui_queue.put(("refresh_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_inspect_ticker(self) -> None:
        sym = self.cek_ticker_var.get().strip().upper().removesuffix(".JK")
        if not sym:
            return
        cid, modal = self._get_profile_and_modal()
        self.status_var.set(f"Menganalisis {sym}...")

        def _worker():
            try:
                detail = dm.inspect_ticker_detail(sym, modal, chat_id=cid)
                png_path = None
                try:
                    res = detail["res"]
                    png_path = chartmod.render_chart(res["ind"], sym, res["sr"], res["score"])
                except Exception:
                    png_path = None
                detail["chart_path"] = png_path
                self.ui_queue.put(("cek_ok", detail))
            except Exception as exc:
                self.ui_queue.put(("cek_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------- queue & render
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "tray_show":
                    self._show_from_tray()
                elif kind == "tray_refresh":
                    self.trigger_refresh_all()
                elif kind == "tray_quit":
                    self._quit_app()
                    return
                elif kind == "refresh_ok":
                    self._refresh_busy = False
                    self._apply_refresh(payload)
                elif kind == "refresh_err":
                    self._refresh_busy = False
                    self.status_var.set(f"Gagal memperbarui: {payload}")
                elif kind == "sinyal_ok":
                    self._render_sinyal(payload)
                    self.status_var.set("Snapshot sinyal diperbarui.")
                elif kind == "scan_ok":
                    self._scan_busy = False
                    self.status_var.set("Scan 944 emiten selesai!")
                    self.trigger_load_sinyal()
                    if self.toast_var.get():
                        send_windows_toast("Scan BEI Selesai", "Snapshot pasar terbaru sudah siap di tab Sinyal & Paket.")
                elif kind == "scan_err":
                    self._scan_busy = False
                    self.status_var.set(f"Scan gagal: {payload}")
                    messagebox.showwarning("Scan BEI", str(payload))
                elif kind == "paket_ok":
                    self._render_paket(payload)
                elif kind == "jurnal_ok":
                    self._render_jurnal(payload)
                elif kind == "cek_ok":
                    self._apply_cek(payload)
                elif kind == "cek_err":
                    self.status_var.set(f"Gagal cek emiten: {payload}")
                    messagebox.showwarning("Cek Emiten", str(payload))
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _apply_refresh(self, data_bundle: dict) -> None:
        pulse = data_bundle["pulse"]
        ihsg = pulse.get("ihsg") or {}
        reg = ihsg.get("regime") or {}
        self._last_regime = reg
        cond = pulse.get("condition") or "-"
        notes = "; ".join(pulse.get("condition_notes") or [])

        close = ihsg.get("close")
        ret1d = ihsg.get("ret1d")
        self.ihsg_close_var.set(f"{close:,.2f}" if close is not None else "tidak tersedia")
        if ret1d is not None:
            self.ihsg_chg_var.set(f"{'▲' if ret1d >= 0 else '▼'} {ret1d:+.2f}%")
            self.ihsg_chip.configure(style=f"Chip.{'green' if ret1d >= 0 else 'red'}.TLabel")
        else:
            self.ihsg_chg_var.set("—")
            self.ihsg_chip.configure(style="Chip.gray.TLabel")

        reg_status = reg.get("status", "NA")
        self.regime_var.set(reg_status)
        reg_color = GREEN if reg_status == "BULL" else RED if reg_status == "BEAR" else AMBER if reg_status == "DEFENSIVE" else MUTED
        self.regime_val.configure(fg=reg_color)
        self.regime_sub_var.set(reg.get("label", "-"))

        self.cond_var.set(cond)
        cond_colors = {"green": GREEN, "red": RED, "amber": AMBER, "blue": BLUE, "gray": MUTED, "accent": BLUE}
        self.cond_val.configure(fg=cond_colors[_chip_for(cond)])
        self.cond_sub_var.set((notes[:80] + "…") if len(notes) > 80 else notes)

        self.update_var.set(pulse.get("as_of_label", "-"))
        self.status_var.set(f"Update terakhir: {pulse.get('as_of_label', '-')}")

        self._render_market_text(pulse)
        self._render_watch_rows(data_bundle["watch_rows"])
        self._render_port_rows(data_bundle["port_rows"])
        self._render_sinyal(data_bundle["sinyal_data"])
        self._render_jurnal(data_bundle["jurnal_data"])

        alerts = dm.detect_action_alerts(
            self.prev_alert_state, data_bundle["watch_rows"], data_bundle["port_rows"]
        )
        for al in alerts:
            self._alert_insert(al)
            if self.toast_var.get():
                send_windows_toast(al["title"], al["message"])

    def _render_market_text(self, pulse: dict) -> None:
        ihsg = pulse.get("ihsg") or {}
        reg = ihsg.get("regime") or {}
        br = pulse.get("breadth") or {}
        sec = pulse.get("sectors") or {}
        news_d = pulse.get("news") or {}
        cond = pulse.get("condition") or "-"
        notes = "; ".join(pulse.get("condition_notes") or [])

        lines = [
            f"=== LAPORAN KONDISI PASAR BEI ({pulse.get('as_of_label', '-')}) ===",
            f"Status Bursa : {'BUKA' if pulse.get('market_open') else 'TUTUP / DI LUAR JAM BURSA'}",
            f"Kondisi      : {cond}" + (f" — {notes}" if notes else ""),
            f"Rezim IHSG   : {reg.get('status', 'NA')} ({reg.get('label', '-')})",
            "",
        ]
        if br.get("ok"):
            lines.append(f"Breadth & penggerak (/scan {br.get('trade_date', '-')}): panel kanan →")
        else:
            lines.append(f"Breadth (/scan): {br.get('reason') or 'belum ada snapshot lokal.'}")

        lines.append("")
        if sec.get("ok"):
            all_secs = sec.get("sectors") or []
            top_s = ", ".join(f"{s['name']} ({s['pct']:+.2f}%)" for s in all_secs[:3])
            bot_s = ", ".join(f"{s['name']} ({s['pct']:+.2f}%)" for s in all_secs[-3:]) if len(all_secs) > 3 else "-"
            lines.append(f"Sektor Terkuat : {top_s or '-'}")
            lines.append(f"Sektor Terlemah: {bot_s or '-'}")
        else:
            lines.append(f"Sektor BEI     : {sec.get('reason') or 'tidak tersedia'}")

        lines.append("")
        if news_d:
            lines.append(f"Sentimen & berita pasar: panel kanan →  (skor {news_d.get('pct', 0):+.1f}%)")
        else:
            lines.append("Berita Pasar   : tidak tersedia")

        self._rich_lines(self.market_text, lines)
        self._render_market_side(pulse)

    def _render_market_side(self, pulse: dict) -> None:
        """Panel kanan tab Kondisi Pasar: bar breadth, penggerak, & kartu sentimen berita."""
        br = pulse.get("breadth") or {}
        news_d = pulse.get("news") or {}

        self.breadth_canvas.delete("all")
        w = max(self.breadth_canvas.winfo_width(), 360)
        adv, dec, flat = br.get("adv", 0) or 0, br.get("dec", 0) or 0, br.get("flat", 0) or 0
        total = adv + dec + flat
        if br.get("ok") and total > 0:
            x = 0
            for val, color in ((adv, GREEN), (dec, RED), (flat, MUTED)):
                seg = int(w * val / total) if val else 0
                if seg:
                    self.breadth_canvas.create_rectangle(x, 0, x + seg, 16, fill=color, outline="")
                x += seg
            self.breadth_counts_var.set(f"Naik {adv} · Turun {dec} · Datar {flat}  (n={br.get('n_usable', total)})")
            self.breadth_ma_var.set(
                f">MA50: {br.get('pct_above_sma50') or 0:.1f}% · >MA200: {br.get('pct_above_sma200') or 0:.1f}% · {br.get('trade_date', '-')}"
            )
            g = ", ".join(f"{m['ticker']} {m['ret1d']:+.1f}%" for m in (br.get("top_gainers") or [])[:5]) or "-"
            l = ", ".join(f"{m['ticker']} {m['ret1d']:+.1f}%" for m in (br.get("top_losers") or [])[:5]) or "-"
        else:
            self.breadth_canvas.create_rectangle(0, 0, w, 16, fill=BORDER, outline="")
            self.breadth_counts_var.set(br.get("reason") or "belum ada snapshot lokal")
            self.breadth_ma_var.set("")
            g = l = "-"
        self.market_gainers.configure(text=f"▲ {g}")
        self.market_losers.configure(text=f"▼ {l}")

        if news_d:
            label = news_d.get("label", "-")
            self.news_chip.configure(text=label, style=f"Chip.{_chip_for(label)}.TLabel")
            self.news_score_var.set(f"skor {news_d.get('pct', 0):+.1f}%")
            heads = "\n".join(f"• {h.get('title', '')}" for h in (news_d.get("details") or [])[:6]) or "—"
        else:
            self.news_chip.configure(text="—", style="Chip.gray.TLabel")
            self.news_score_var.set("berita tidak tersedia")
            heads = "—"
        self.news_headlines.configure(text=heads)

    def _render_watch_rows(self, rows: list[dict]) -> None:
        self.watch_tree.delete(*self.watch_tree.get_children())
        counts: dict[str, int] = {}
        for i, r in enumerate(rows):
            act = r.get("action") or "TIDAK TERSEDIA"
            counts[act] = counts.get(act, 0) + 1
            tags = (act, "odd") if i % 2 else (act,)
            self.watch_tree.insert(
                "", tk.END,
                values=(
                    r["ticker"], _fmt_rp(r.get("price")), _fmt_pct(r.get("ret1d")), act,
                    f"{r['score_pct']:+.0f}%" if r.get("score_pct") is not None else "-",
                    _fmt_rp(r.get("entry")), _fmt_rp(r.get("sl")), _fmt_rp(r.get("tp")),
                    r.get("lots") or 0, r.get("reason") or "",
                ),
                tags=tags,
            )
        if rows:
            self._hide_empty(self.watch_empty)
        else:
            self._show_empty(self.watch_empty)
        self.watch_foot_var.set(f"{len(rows)} emiten di watchlist")
        self._chip_row(self.watch_chips, counts, ("BELI", "SIAP BELI", "TAHAN", "JUAL", "TIDAK TERSEDIA"))

    def _render_sinyal(self, data_s: dict) -> None:
        meta = data_s.get("meta") or {}
        counts = data_s.get("counts") or {}
        self.sinyal_meta_var.set(f"Snapshot {meta.get('trade_date', '-')} · filter: {self.sinyal_filter_var.get()}"
                                 + (" · hanya BELI berkualitas" if self.sinyal_quality_var.get() else ""))
        self.sinyal_tree.delete(*self.sinyal_tree.get_children())
        rows = data_s.get("rows") or []
        for i, r in enumerate(rows):
            sig = r.get("signal") or "TAHAN"
            rp = r.get("rel_pct")
            tags = (sig, "odd") if i % 2 else (sig,)
            self.sinyal_tree.insert(
                "", tk.END,
                values=(
                    r["ticker"], (r.get("name") or "")[:24], _fmt_rp(r.get("price")),
                    _fmt_pct(r.get("ret1d")), sig,
                    f"{rp:.0f}" if rp is not None else "-",
                    _fmt_rp(r.get("entry")), _fmt_rp(r.get("sl")), _fmt_rp(r.get("tp")),
                    r.get("quality_mark", "❔"), r.get("reason") or "",
                ),
                tags=tags,
            )
        if rows:
            self._hide_empty(self.sinyal_empty)
        else:
            self._show_empty(self.sinyal_empty)
        self._chip_row(self.sinyal_chips, counts, ("BELI", "JUAL", "TAHAN"))

    def _render_paket(self, res: dict) -> None:
        self.paket_tree.delete(*self.paket_tree.get_children())
        if not res.get("ok"):
            self.paket_info_var.set(res.get("reason") or "Tidak ada kandidat paket yang lolos saringan.")
            self.status_var.set("Paket Beli: kosong.")
            self._show_empty(self.paket_empty)
            return
        plans = res.get("plans") or []
        if not plans:
            self.paket_info_var.set("Tidak ada kandidat paket yang lolos saringan.")
            self._show_empty(self.paket_empty)
            return
        self._hide_empty(self.paket_empty)
        self.paket_info_var.set(f"{len(plans)} kandidat paket beli siap Stockbit PC (klik ganda baris untuk cek detail).")
        for i, p in enumerate(plans):
            self.paket_tree.insert(
                "", tk.END,
                values=(
                    p["ticker"], p.get("grade", "-"), _fmt_rp(p.get("entry")),
                    _fmt_rp(p.get("sl")), _fmt_rp(p.get("tp")), p.get("lots", 0),
                    _fmt_rp(p.get("value")), _fmt_rp(p.get("real_risk")),
                    f"{p.get('rr', 0):.1f}" if p.get("rr") is not None else "-",
                    "; ".join(p.get("warnings") or []) or "Lolos saringan kualitas & risiko",
                ),
                tags=("odd",) if i % 2 else (),
            )
        self.status_var.set(f"Paket Beli disusun ({len(plans)} emiten).")

    def _render_port_rows(self, rows: list[dict]) -> None:
        self.port_tree.delete(*self.port_tree.get_children())
        for i, p in enumerate(rows):
            st = p.get("thesis") or "TETAP"
            tags = (st, "odd") if i % 2 else (st,)
            self.port_tree.insert(
                "", tk.END,
                values=(
                    p["ticker"], p["lots"], _fmt_rp(p.get("avg_price")), _fmt_rp(p.get("price")),
                    _fmt_pct(p.get("pnl_net_pct")), _fmt_rp(p.get("effective_sl")),
                    _fmt_rp(p.get("trailing_sl")), _fmt_rp(p.get("tp")), st,
                    "; ".join((p.get("reasons") or [])[:2]),
                ),
                tags=tags,
            )
        if rows:
            self._hide_empty(self.port_empty)
        else:
            self._show_empty(self.port_empty)
        self.port_foot_var.set(f"{len(rows)} posisi terbuka" if rows else "")

    def _render_jurnal(self, data_j: dict) -> None:
        body = _strip_html(data_j.get("summary_text") or "")
        hist = _strip_html(data_j.get("history_text") or "")
        full = body + ("\n\n" + hist if hist else "")
        self._rich_lines(self.jurnal_text, (full or "Belum ada catatan jurnal.").splitlines())

    def _apply_cek(self, detail: dict) -> None:
        sym = detail["ticker"]
        res = detail["res"]
        snap = res["snap"]
        sc = res["score"]
        cek_r = detail["cek"]
        plan = detail.get("plan")
        held = detail.get("held")

        lines = [
            f"=== CEK EMITEN: {sym} ({detail.get('generated_at', '-')}) ===",
            f"Harga Terakhir : {_fmt_rp(snap.get('close'))} (as of {res.get('as_of')})",
            f"Skor Teknikal  : {sc.get('pct', 0):+.1f}% ({sc.get('trend_label') or sc.get('rating')})",
            f"VERDICT        : {cek_r.get('verdict', '-')} — {cek_r.get('summary', '')}",
            "",
            "--- Sudut Pandang Pro (Bull) ---",
        ]
        for b in cek_r.get("pro") or ["(belum ada poin kuat)"]:
            lines.append(f"  + {b}")
        lines.append("")
        lines.append("--- Risiko / Pertimbangan (Bear) ---")
        for r in cek_r.get("risks") or ["(tidak ada catatan risiko utama)"]:
            lines.append(f"  - {r}")
        if plan:
            lines += [
                "",
                "--- Rencana Ukuran & Level (Stockbit PC) ---",
                f"  Entry Limit : {_fmt_rp(plan.get('entry'))} | SL: {_fmt_rp(plan.get('sl'))} | TP: {_fmt_rp(plan.get('tp'))}",
                f"  Saran Lot   : {plan.get('lots', 0)} lot (Total {reports.fmt_rp(plan.get('cost') or 0)})",
            ]
        if held:
            lines += ["", f"Posisi Anda saat ini: {held.get('lots')} lot @ {_fmt_rp(held.get('avg_price'))}"]

        self._rich_lines(self.cek_text, lines)

        png_path = detail.get("chart_path")
        if png_path and os.path.exists(png_path):
            try:
                img = tk.PhotoImage(file=png_path)
                if img.width() > 620:
                    factor = max(1, img.width() // 580)
                    img = img.subsample(factor, factor)
                self._chart_img = img
                self.chart_label.configure(image=img, text="")
            except Exception:
                self.chart_label.configure(image="", text="Gagal memuat gambar grafik.")
        else:
            self.chart_label.configure(image="", text="Grafik tidak tersedia.")
        self.status_var.set(f"Cek {sym} selesai ({cek_r.get('verdict', '-')}).")


def main() -> None:
    root = tk.Tk()
    SahamBotDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""SahamBot Desktop — terminal pemantauan & analisis saham BEI (offline-ready GUI).

Menyajikan 8 panel utama (Pasar & Rezim, Watchlist, Sinyal Pasar, Paket
Rekomendasi, Portofolio Asli, Jurnal & Evaluasi, Cek Emiten, Alert & Riwayat)
yang membaca mesin logika ``core/*`` dan SQLite ``store.db`` yang sama dengan
bot Telegram. Tanpa auto-trading atau penyimpanan kredensial broker: semua
eksekusi dilakukan secara manual oleh pengguna di Stockbit.

Struktur modular:
  - ``core/desktop_helpers.py``: helper tampilan & formatting murni (tanpa ``tkinter``).
  - ``desktop_ui/widgets.py``: palet tema, font, ``Switch``, KPI card, & Treeview.
  - ``desktop_ui/tab_*.py``: mixin builder/refresh/aksi untuk masing-masing dari 8 tab.
"""
from __future__ import annotations

import ctypes
from datetime import datetime
import logging
import queue
import sys
import threading
import time
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from PIL import ImageTk
except Exception:  # pragma: no cover - environment headless tanpa Tcl/Tk PhotoImage
    ImageTk = None

try:
    import pystray
except ImportError:
    pystray = None

from core import desktop_monitor as dm
from core import marketcal
from core import store_profile
from core import store_watchlist
from core import textutil
from core.desktop_helpers import (  # noqa: F401 - re-export untuk kompatibilitas mundur
    ALERT_KIND_ORDER,
    APP_ID,
    DEFAULT_MODAL_RP,
    DESKTOP_CHAT_ID,
    SECTOR_ALIAS_IDX_IC,
    SPINNER_FRAMES,
    VALID_REFRESH_MIN,
    _fmt_arrow_pct,
    _fmt_num,
    _fmt_pct,
    _fmt_rp,
    _news_item_tag,
    _parse_sort_key,
    _safe_float,
    _safe_int,
    _strip_html,
    busy_text,
    classify_line,
    countdown_text,
    create_tray_icon_image,
    format_plan_levels,
    format_pullback_sub,
    hit_test_rects,
    inline_segments,
    market_clock_text,
    match_sector_option,
    record_desktop_execution,
    spinner_frame,
    tab_label,
)
from desktop_ui import (
    AlertsTabMixin,
    CekTabMixin,
    JurnalTabMixin,
    PaketTabMixin,
    PasarTabMixin,
    PortofolioTabMixin,
    SinyalTabMixin,
    WatchlistTabMixin,
)
from desktop_ui.widgets import (  # noqa: F401 - re-export untuk kompatibilitas mundur
    FONT_KPI,
    FONT_MONO,
    FONT_MONO_BOLD,
    FONT_SMALL,
    FONT_SMALL_BOLD,
    FONT_TITLE,
    FONT_UI,
    FONT_UI_BOLD,
    THEME,
    Switch,
    _build_treeview,
    _configure_tree_tags,
    _enable_dark_titlebar,
    _make_kpi_card,
    _populate_tree,
    _set_kpi,
    _sort_treeview,
    attach_tooltip,
    send_windows_toast,
)

log = logging.getLogger(__name__)


class SahamBotDesktopApp(
    PasarTabMixin,
    WatchlistTabMixin,
    SinyalTabMixin,
    PaketTabMixin,
    PortofolioTabMixin,
    JurnalTabMixin,
    CekTabMixin,
    AlertsTabMixin,
):
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
            selectbackground=THEME["card"],
            selectforeground=THEME["fg"],
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", THEME["card"])],
            foreground=[("readonly", THEME["fg"])],
            selectbackground=[("readonly", THEME["card"])],
            selectforeground=[("readonly", THEME["fg"])],
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
        # ---- Top Command Bar (2 baris agar tidak terpotong di layar sempit) ----
        topbar = tk.Frame(
            self.root,
            bg=THEME["bg_elev"],
            highlightbackground=THEME["border"],
            highlightthickness=1,
            padx=12,
            pady=6,
        )
        topbar.pack(fill="x")

        top_row1 = tk.Frame(topbar, bg=THEME["bg_elev"])
        top_row1.pack(fill="x", pady=(0, 6))

        brand = tk.Frame(top_row1, bg=THEME["bg_elev"])
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
            top_row1,
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
            top_row1,
            text="--:--:-- WIB",
            bg=THEME["card"],
            fg=THEME["dim"],
            padx=10,
            pady=4,
            font=FONT_MONO_BOLD,
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.lbl_clock.pack(side="right")

        # Baris 2: Kontrol Profil, Modal, Switch & Tombol Aksi
        right_bar = tk.Frame(topbar, bg=THEME["bg_elev"])
        right_bar.pack(fill="x")

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
        btn_simpan = ttk.Button(
            right_bar, text="Simpan", style="Ghost.TButton", command=self._save_profile,
        )
        btn_simpan.pack(side="left", padx=(0, 8))
        attach_tooltip(btn_simpan, "Simpan profil & modal ke database lokal (Enter pada input)")

        tk.Frame(right_bar, bg=THEME["border"], width=1, height=22).pack(
            side="left", padx=(0, 8), fill="y",
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
        attach_tooltip(self.sw_syariah, "Saring Watchlist, Sinyal & Paket hanya saham syariah (DES OJK)")

        self.sw_toast = Switch(
            right_bar,
            text="Toast",
            variable=self.var_toast,
            command=lambda: self._save_ui_state(),
            on_color=THEME["accent"],
            bg=THEME["bg_elev"],
        )
        self.sw_toast.pack(side="left", padx=(0, 8))
        attach_tooltip(self.sw_toast, "Tampilkan notifikasi Windows saat ada sinyal BUY/JUAL atau perubahan tesis")

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
        attach_tooltip(cb_auto, "Interval auto-refresh Watchlist & Portofolio di background")

        tk.Frame(right_bar, bg=THEME["border"], width=1, height=22).pack(
            side="left", padx=(0, 8), fill="y",
        )

        self.btn_refresh = ttk.Button(
            right_bar, text="↻ Refresh", style="Accent.TButton", command=self.refresh_active_tab,
        )
        self.btn_refresh.pack(side="left", padx=(0, 4))
        attach_tooltip(self.btn_refresh, "Segarkan tab yang sedang dibuka (F5 / Ctrl+R)")
        self.btn_all = ttk.Button(
            right_bar, text="Semua Tab", style="Ghost.TButton", command=self.refresh_all,
        )
        self.btn_all.pack(side="left")
        attach_tooltip(self.btn_all, "Segarkan Pasar, Watchlist, Sinyal, Paket, Portofolio & Jurnal")
        # Kedua tombol topbar ikut menunjukkan estado sibuk saat ada proses berjalan
        self._global_busy_buttons = [self.btn_refresh, self.btn_all]
        if pystray is not None:
            btn_tray = ttk.Button(
                right_bar, text="🗗 Ke Tray", style="Ghost.TButton",
                command=self._on_close_or_tray,
            )
            btn_tray.pack(side="left", padx=(4, 0))
            attach_tooltip(btn_tray, "Sembunyikan ke System Tray (tetap memantau di background)")
        btn_help = ttk.Button(
            right_bar, text="?", width=3, style="Ghost.TButton",
            command=self._open_shortcut_dialog,
        )
        btn_help.pack(side="left", padx=(4, 0))
        attach_tooltip(btn_help, "Panduan pintasan keyboard & tips cepat (F1)")

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
                "Data ±15 menit delay · Tanpa auto-trading (eksekusi manual di Stockbit) · "
                "F1: Pintasan Keyboard"
            ),
            bg=THEME["bg_elev"],
            fg=THEME["dim"],
            font=FONT_SMALL,
            anchor="e",
        ).pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F1>", lambda _e: self._open_shortcut_dialog())
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

    def _open_shortcut_dialog(self) -> None:
        """Tampilkan dialog ringkas pintasan keyboard & panduan navigasi."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Pintasan Keyboard & Panduan Cepat")
        dlg.configure(bg=THEME["bg"])
        dlg.transient(self.root)
        dlg.resizable(False, False)
        _enable_dark_titlebar(dlg)

        box = tk.Frame(dlg, bg=THEME["bg"], padx=18, pady=14)
        box.pack(fill="both", expand=True)
        tk.Label(
            box, text="PINTASAN KEYBOARD SAHAMBOT",
            bg=THEME["bg"], fg=THEME["accent"], font=FONT_UI_BOLD,
        ).pack(anchor="w", pady=(0, 8))

        shortcuts = [
            ("F5 / Ctrl+R", "Segarkan tab yang sedang aktif"),
            ("Ctrl+K", "Fokus ke input Cek Emiten (Tab 7)"),
            ("Ctrl+F", "Fokus ke kotak pencarian pada tab aktif"),
            ("Ctrl+1 .. 8", "Pindah cepat ke tab 1 sampai 8"),
            ("Ctrl + / -", "Perbesar / perkecil ukuran huruf"),
            ("Ctrl+0", "Reset ukuran huruf ke 100%"),
            ("Space / Enter", "Toggle Switch (saat fokus) atau jalankan input"),
            ("Delete", "Hapus emiten terpilih di Watchlist"),
            ("Klik Ganda", "Buka detail Cek Emiten dari baris tabel"),
            ("Klik Kanan", "Menu aksi cepat pada baris tabel"),
            ("F1", "Buka dialog pintasan ini"),
        ]
        grid = tk.Frame(box, bg=THEME["card"], highlightbackground=THEME["border"], highlightthickness=1, padx=12, pady=10)
        grid.pack(fill="x", pady=(0, 12))
        for idx, (key, desc) in enumerate(shortcuts):
            tk.Label(
                grid, text=key, bg=THEME["card"], fg=THEME["accent"],
                font=FONT_MONO_BOLD, width=16, anchor="w",
            ).grid(row=idx, column=0, sticky="w", pady=2)
            tk.Label(
                grid, text=desc, bg=THEME["card"], fg=THEME["fg"],
                font=FONT_SMALL, anchor="w",
            ).grid(row=idx, column=1, sticky="w", padx=(10, 0), pady=2)

        ttk.Button(box, text="Tutup (Esc)", style="Accent.TButton", command=dlg.destroy).pack(anchor="e")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.focus_set()

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

    def _selected_ticker(self, tree: ttk.Treeview, col_index: int = 0) -> str | None:
        sel = tree.selection()
        if not sel:
            return None
        vals = tree.item(sel[0], "values")
        if not vals or len(vals) <= col_index:
            return None
        sym = str(vals[col_index]).strip().upper()
        return sym if sym and sym != "-" else None

    def _focus_active_search(self) -> None:
        idx = self.notebook.index(self.notebook.select())
        if idx == 1:
            self.ent_watch_search.focus_set()
        elif idx == 2:
            self.ent_sinyal_search.focus_set()
        else:
            self._focus_cek_ticker()

    def _show_popup(self, event: tk.Event, items: list[tuple[str, Callable[[], None]]]) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=THEME["card"], fg=THEME["fg"],
                       activebackground=THEME["accent_soft"], activeforeground=THEME["accent"])
        for label, cb in items:
            menu.add_command(label=label, command=cb)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_text(self, text: str, status_msg: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.var_status.set(status_msg)

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

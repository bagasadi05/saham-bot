"""Komponen widget, tema warna, utilitas Treeview & notifikasi Windows SahamBot Desktop."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any, Callable

import tkinter as tk
from tkinter import font as tkfont, ttk

from core.desktop_helpers import _parse_sort_key, _strip_html

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


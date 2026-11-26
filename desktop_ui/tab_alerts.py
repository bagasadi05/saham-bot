"""Tab 8: Riwayat alert sinyal/tesis real-time dan notifikasi desktop."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk

from desktop_ui.widgets import (
    FONT_SMALL,
    THEME,
    _build_treeview,
    _populate_tree,
    send_windows_toast,
)


class AlertsTabMixin:
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

    def _alert_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_alerts, 2))

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
        empty_msg = (
            "Belum ada alert yang terpicu pada sesi ini.\n"
            "Alert akan muncul otomatis saat sinyal Watchlist berubah atau tesis Portofolio menjadi GUGUR/MELEMAH."
        )
        _populate_tree(self.tree_alerts, table_rows, empty_text=empty_msg)

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


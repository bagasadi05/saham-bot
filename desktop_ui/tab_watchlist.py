"""Tab 2: Watchlist emiten, zona beli, dan editor alert harga/indikator."""
from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from core import alerts as alertsmod
from core import desktop_monitor as dm
from core import store_watchlist
from core.desktop_helpers import (
    ALERT_KIND_ORDER,
    _fmt_arrow_pct,
    _fmt_num,
    _strip_html,
)
from desktop_ui.widgets import (
    FONT_SMALL,
    FONT_UI_BOLD,
    THEME,
    _build_treeview,
    _enable_dark_titlebar,
    _make_kpi_card,
    _populate_tree,
    _set_kpi,
)


class WatchlistTabMixin:
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

    def _watch_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_watch, 0))

    def _watch_prefill_buy(self) -> None:
        sym = self._selected_ticker(self.tree_watch, 0)
        if not sym:
            return
        row = next((r for r in self._last_watch_rows if r.get("ticker") == sym), None)
        self._prefill_order_form(sym, "BELI", (row or {}).get("entry") or (row or {}).get("price"), 1)

    def _watch_goto_sinyal(self, sym: str | None) -> None:
        if sym:
            self.var_sinyal_search.set(str(sym).upper())
        self._goto_tab(2, self.refresh_sinyal)

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

    def _export_watch_csv(self) -> None:
        rows = [list(self.tree_watch.item(i, "values")) for i in self.tree_watch.get_children("")]
        hdr = [self.tree_watch.heading(c, "text") for c in self.tree_watch["columns"]]
        self._export_tree_csv("sahambot_watchlist.csv", hdr, rows)


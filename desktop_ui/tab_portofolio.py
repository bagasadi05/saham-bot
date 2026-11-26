"""Tab 5: Portofolio asli, pelacak tesis posisi, batas risiko, dan catat eksekusi."""
from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core import desktop_monitor as dm
from core import risk as riskmod
from core import store_portfolio
from core import textutil
from core.desktop_helpers import (
    _fmt_arrow_pct,
    _fmt_num,
    _fmt_pct,
    _fmt_rp,
    _safe_float,
    _safe_int,
    record_desktop_execution,
)
from desktop_ui.widgets import (
    FONT_SMALL,
    FONT_SMALL_BOLD,
    THEME,
    _build_treeview,
    _make_kpi_card,
    _populate_tree,
    _set_kpi,
)


class PortofolioTabMixin:
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

    def refresh_port(self) -> None:
        cid = self._current_chat_id()
        reg = self._regime_ctx()

        def _work() -> dict[str, Any]:
            rows = dm.evaluate_portfolio_rows(cid, market_regime=reg)
            summary = dm.compute_portfolio_risk_summary(cid, rows)
            return {"rows": rows, "summary": summary}

        self._run_async("port", _work, "port")

    def _apply_port(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {}
        rows = data.get("rows") or []
        summary = data.get("summary") or {}
        self._last_port_rows = rows
        self._last_port_summary = summary
        self._update_tab_badges()

        n_pos = _safe_int(summary.get("n_positions"))
        priced_cnt = _safe_int(summary.get("priced_count"))
        tot_val = summary.get("total_value_rp")
        tot_cost = _safe_float(summary.get("total_cost_rp"), 0.0) or 0.0
        tot_pnl = summary.get("total_pnl_rp")
        pnl_pct = summary.get("total_pnl_pct")
        risk_rp = _safe_float(summary.get("risk_to_sl_rp"), 0.0) or 0.0
        risk_pct = summary.get("risk_to_sl_pct")
        var95 = summary.get("var95_rp")
        es95 = summary.get("es95_rp")
        pos_lim = _safe_float(
            summary.get("max_pos_limit_pct")
            or ((summary.get("max_pos_pct") or 0.2) * 100.0), 20.0,
        ) or 20.0
        sec_lim = _safe_float(
            summary.get("max_sec_limit_pct")
            or ((summary.get("max_sector_pct") or 0.4) * 100.0), 40.0,
        ) or 40.0

        val_txt = "tidak tersedia" if (n_pos > 0 and priced_cnt == 0) else _fmt_rp(tot_val)
        _set_kpi(
            self.pt_val,
            val_txt,
            f"Cost: {_fmt_rp(tot_cost)} ({n_pos} posisi)",
        )
        if tot_pnl is None:
            _set_kpi(self.pt_pnl, "tidak tersedia" if n_pos > 0 else "-", "harga pasar belum tersedia", color=THEME["muted"])
        else:
            pnl_v = _safe_float(tot_pnl, 0.0) or 0.0
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

    def _port_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_port, 0))

    def _port_prefill_sell(self) -> None:
        sym = self._selected_ticker(self.tree_port, 0)
        if not sym:
            return
        row = next((r for r in self._last_port_rows if r.get("ticker") == sym), None)
        if row:
            self._prefill_order_form(sym, "JUAL", row.get("price") or row.get("avg_price"), row.get("lots") or 1)

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

    def _export_port_csv(self) -> None:
        rows = [list(self.tree_port.item(i, "values")) for i in self.tree_port.get_children("")]
        hdr = [self.tree_port.heading(c, "text") for c in self.tree_port["columns"]]
        self._export_tree_csv("sahambot_portofolio.csv", hdr, rows)

    def _prefill_order_form(self, sym: str, side: str, price: Any, lots: Any) -> None:
        self.var_port_ticker.set(sym)
        self.var_port_side.set(side)
        px = _safe_float(price)
        self.var_port_price.set(str(int(round(px))) if px else "")
        self.var_port_lots.set(str(max(1, int(_safe_float(lots, 1) or 1))))
        self.notebook.select(4)

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


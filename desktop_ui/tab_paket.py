"""Tab 4: Paket Rekomendasi beli sesuai modal, rezim IHSG, dan batas risiko."""
from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import ttk

from core import desktop_monitor as dm
from core.desktop_helpers import _fmt_num, _fmt_pct, _fmt_rp, _safe_float
from desktop_ui.widgets import (
    FONT_SMALL,
    THEME,
    _build_treeview,
    _make_kpi_card,
    _populate_tree,
    _set_kpi,
)


class PaketTabMixin:
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
        self.pk_modal = _make_kpi_card(kpi, "Modal & Skala Rezim", tooltip="Modal aktif dan faktor pengali eksposur berdasar rezim IHSG")
        self.pk_alloc = _make_kpi_card(kpi, "Total Alokasi Paket", tooltip="Total estimasi biaya beli seluruh kandidat paket + fee broker")
        self.pk_cash = _make_kpi_card(kpi, "Sisa Kas", tooltip="Sisa dana tunai yang tidak dialokasikan")
        self.pk_risk = _make_kpi_card(kpi, "Total Risiko ke SL", tooltip="Estimasi rugi maksimal bila seluruh posisi paket menyentuh Stop-Loss")
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

    def _apply_paket(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {}
        self._last_paket_res = data
        plans = data.get("plans") or []
        self._update_tab_badges()
        scale = _safe_float(data.get("scale"), 1.0) or 1.0
        modal = _safe_float(data.get("modal"), 0.0) or 0.0
        alloc = _safe_float(data.get("total_alloc_rp"), 0.0) or 0.0
        cash_left = _safe_float(data.get("cash_left_rp"), 0.0) or 0.0
        tot_risk = _safe_float(data.get("total_risk_rp"), 0.0) or 0.0

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
        empty_msg = (
            data.get("reason")
            or "Belum ada paket beli yang lolos saringan.\nJalankan '⚡ Scan BEI (~944)' di tab Sinyal Pasar atau klik '↻ Hitung Ulang Paket'."
        )
        _populate_tree(self.tree_paket, table_rows, empty_text=empty_msg)

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

    def _paket_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_paket, 1))

    def _paket_prefill_buy(self) -> None:
        sym = self._selected_ticker(self.tree_paket, 1)
        if not sym:
            return
        plans = (self._last_paket_res or {}).get("plans") or []
        plan = next((p for p in plans if p.get("ticker") == sym), None)
        if plan:
            self._prefill_order_form(sym, "BELI", plan.get("entry"), plan.get("lots") or 1)

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

    def _copy_paket_orders(self) -> None:
        plans = (self._last_paket_res or {}).get("plans") or []
        txt = dm.format_order_ticket_text(plans)
        self._copy_text(txt, f"Tiket order Stockbit ({len(plans)} emiten) disalin ke clipboard.")

    def _export_paket_csv(self) -> None:
        rows = [list(self.tree_paket.item(i, "values")) for i in self.tree_paket.get_children("")]
        hdr = [self.tree_paket.heading(c, "text") for c in self.tree_paket["columns"]]
        self._export_tree_csv("sahambot_paket.csv", hdr, rows)


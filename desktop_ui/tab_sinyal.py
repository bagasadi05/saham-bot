"""Tab 3: Sinyal Pasar BEI, filter sektor/syariah/kualitas, dan scan penuh."""
from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from core import desktop_monitor as dm
from core.desktop_helpers import _fmt_arrow_pct, _fmt_num, _safe_float, _safe_int
from desktop_ui.widgets import (
    FONT_SMALL,
    FONT_SMALL_BOLD,
    Switch,
    THEME,
    _build_treeview,
    _make_kpi_card,
    _populate_tree,
    _set_kpi,
    send_windows_toast,
)


class SinyalTabMixin:
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
        self.sk_scan_date = _make_kpi_card(kpi, "Snapshot /scan", tooltip="Tanggal bar terakhir hasil pemindaian seluruh emiten BEI")
        self.sk_matched = _make_kpi_card(kpi, "Hasil Filter", tooltip="Jumlah emiten yang lolos filter sinyal, sektor, syariah, dan kualitas")
        self.sk_beli = _make_kpi_card(kpi, "Total BELI", tooltip="Total emiten berstatus BELI pada snapshot /scan terakhir")
        self.sk_tahan = _make_kpi_card(kpi, "Total TAHAN", tooltip="Total emiten berstatus TAHAN / netral pada snapshot /scan")
        self.sk_jual = _make_kpi_card(kpi, "Total JUAL", tooltip="Total emiten berstatus JUAL (hindari beli / tinjau kurangi)")
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
        if not isinstance(res, dict):
            res = {}
        cnt = _safe_int(res.get("count") or res.get("scanned"))
        self.var_status.set(f"Scan BEI selesai ({cnt} emiten diproses). Memperbarui Sinyal, Paket & Pasar...")
        if self.var_toast.get():
            send_windows_toast("Scan BEI Selesai", "Snapshot sinyal & breadth pasar telah diperbarui.")
        self.refresh_sinyal()
        self.refresh_paket()
        self.refresh_market()

    def _apply_sinyal(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {}
        rows = data.get("rows") or []
        self._last_sinyal_rows = rows
        self._update_tab_badges()
        counts = data.get("counts") or {}
        meta = data.get("meta") or {}
        sectors = ["Semua Sektor"] + list(data.get("sectors") or [])
        self.cb_sinyal_sector.configure(values=sectors)

        tot_scan = sum(_safe_int(v) for v in counts.values()) or len(rows)
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
        empty_msg = (
            "Belum ada data snapshot /scan atau tidak ada emiten yang lolos filter.\n"
            "Klik '⚡ Scan BEI (~944)' untuk memindai pasar atau longgarkan filter."
        )
        _populate_tree(self.tree_sinyal, table_rows, empty_text=empty_msg)
        self.var_status.set(f"Sinyal pasar dimuat: {len(rows)} baris ditampilkan.")

    def _sinyal_open_cek(self) -> None:
        self._open_cek_ticker(self._selected_ticker(self.tree_sinyal, 0))

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

    def _export_sinyal_csv(self) -> None:
        rows = [list(self.tree_sinyal.item(i, "values")) for i in self.tree_sinyal.get_children("")]
        hdr = [self.tree_sinyal.heading(c, "text") for c in self.tree_sinyal["columns"]]
        self._export_tree_csv("sahambot_sinyal.csv", hdr, rows)


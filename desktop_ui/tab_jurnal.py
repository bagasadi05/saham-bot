"""Tab 6: Jurnal eksekusi Stockbit, diagnostik MAE/MFE, R-multiple & alpha."""
from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import scrolledtext, ttk

from core import desktop_monitor as dm
from core.desktop_helpers import _fmt_pct, _fmt_rp, _safe_float, _safe_int
from desktop_ui.widgets import FONT_MONO, FONT_SMALL, THEME, _make_kpi_card, _set_kpi


class JurnalTabMixin:
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

    def refresh_jurnal(self) -> None:
        cid = self._current_chat_id()
        try:
            lim = max(10, min(200, int(self.var_jurnal_limit.get())))
        except ValueError:
            lim = 50
        self._run_async("jurnal", lambda: dm.build_journal_summary(cid, limit=lim), "jurnal")

    def _apply_jurnal(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {}
        self._last_jurnal_res = data
        kpis = data.get("kpis") or {}
        diag = data.get("diag") or {}

        n_exec = _safe_int(kpis.get("n_exec"))
        _set_kpi(
            self.jk_execs,
            str(n_exec),
            f"BELI {kpis.get('n_buys', 0)} · JUAL {kpis.get('n_sells', 0)}",
        )
        wr = _safe_float(kpis.get("win_rate_pct"))
        _set_kpi(
            self.jk_wr,
            f"{wr:.1f}%" if wr is not None else "-",
            f"{kpis.get('n_closed', 0)} trade tertutup",
            color=THEME["green"] if (wr or 0) >= 50 else THEME["amber"],
        )
        pf = _safe_float(kpis.get("profit_factor"))
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
        avg_r = _safe_float(kpis.get("avg_r_multiple"))
        alpha = _safe_float(kpis.get("mean_alpha_pct"))
        mae = _safe_float(kpis.get("avg_mae_pct"))
        _set_kpi(
            self.jk_alpha,
            f"{avg_r:+.2f}R" if avg_r is not None else "-",
            f"Alpha vs IHSG: {_fmt_pct(alpha)} · MAE: {_fmt_pct(mae)}",
            color=THEME["green"] if (alpha or 0) > 0 else THEME["muted"],
        )

        sections = [str(data.get("summary_text") or "")]
        if diag and diag.get("n_trades"):
            avg_r_diag = _safe_float(diag.get("avg_r_multiple"))
            cap_ratio = _safe_float(diag.get("avg_capture_ratio"))
            win_bench = _safe_float(diag.get("win_vs_bench_pct"))
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


"""Tab 7: Cek Emiten mendalam (6 poin, forensik, sinyal lanjutan, chart, kartu rencana)."""
from __future__ import annotations

import os
import shutil
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image

try:
    from PIL import ImageTk
except Exception:  # pragma: no cover - environment headless tanpa Tcl/Tk PhotoImage
    ImageTk = None

import config
from core import chart as chartmod
from core import desktop_monitor as dm
from core import store_profile
from core.desktop_helpers import (
    _fmt_arrow_pct,
    _fmt_num,
    _fmt_pct,
    _fmt_rp,
    _news_item_tag,
    _safe_float,
    format_plan_levels,
    format_pullback_sub,
)
from desktop_ui.widgets import (
    FONT_SMALL,
    FONT_SMALL_BOLD,
    FONT_UI,
    FONT_UI_BOLD,
    THEME,
    _make_kpi_card,
    _set_kpi,
)


class CekTabMixin:
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
                include_bandar=True,
            )
            if not isinstance(detail, dict):
                detail = {"ok": False, "reason": "Analisis mengembalikan respons tidak valid.", "ticker": sym}
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
                    _safe_float((res.get("score") or {}).get("pct"), 0.0) or 0.0,
                    out_png,
                    levels=levels or None,
                )
            detail["chart_path"] = chart_path
            return detail

        self._run_async("cek", _work, "cek")

    def _apply_cek(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {"ok": False, "reason": "Respons analisis tidak valid."}
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
        self._set_plan_cards(format_plan_levels(plan), pullback_sub=format_pullback_sub(plan))
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
            pb_sub = format_pullback_sub(plan) if plan.get("entry_pullback") else "-"
            lines.append(
                f"Entry: {_fmt_num(plan.get('entry'), 0)} "
                f"({pb_sub})  |  "
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

        adv = data.get("advanced") or {}
        adv_lines: list[str] = []
        rel_pct = _safe_float(adv.get("rel_pct"))
        if rel_pct is not None:
            adv_lines.append(f"  • Peringkat Relatif OOS (/scan): persentil {rel_pct:.0f}%")
        cv = _safe_float(adv.get("cmf20"))
        if cv is not None:
            c_lbl = "akumulasi" if cv >= 0.12 else ("distribusi" if cv <= -0.12 else "netral")
            adv_lines.append(f"  • Arus Uang CMF20: {cv:+.2f} ({c_lbl})")
        div = adv.get("rsi_divergence")
        if isinstance(div, dict):
            adv_lines.append(f"  • Divergensi RSI14: {div.get('kind', '-').upper()}")
        poc = adv.get("poc")
        if isinstance(poc, dict) and poc.get("poc"):
            adv_lines.append(
                f"  • Volume Profile POC (60h): {_fmt_num(poc['poc'], 0)} ({poc.get('position', '')})"
            )
        if adv.get("candle_patterns"):
            adv_lines.append("  • Pola Candle: " + ", ".join(str(p) for p in adv["candle_patterns"]))
        em = adv.get("expected_move")
        if isinstance(em, dict) and isinstance(em.get("bands"), dict):
            b20 = em["bands"].get(20) if isinstance(em["bands"].get(20), dict) else None
            b5 = em["bands"].get(5) if isinstance(em["bands"].get(5), dict) else None
            parts = []
            if b5:
                p5 = _safe_float(b5.get("pct"))
                parts.append(f"5h {_fmt_num(b5.get('low'), 0)}–{_fmt_num(b5.get('high'), 0)} (±{p5:.1f}%)" if p5 is not None else f"5h {_fmt_num(b5.get('low'), 0)}–{_fmt_num(b5.get('high'), 0)}")
            if b20:
                p20 = _safe_float(b20.get("pct"))
                parts.append(f"20h {_fmt_num(b20.get('low'), 0)}–{_fmt_num(b20.get('high'), 0)} (±{p20:.1f}%)" if p20 is not None else f"20h {_fmt_num(b20.get('low'), 0)}–{_fmt_num(b20.get('high'), 0)}")
            if parts:
                adv_lines.append("  • Rentang Gerak ±1σ: " + " | ".join(parts))
        odds = adv.get("tp_sl_odds")
        if isinstance(odds, dict) and odds.get("n"):
            tp_r = _safe_float(odds.get("tp_rate_pct"))
            sl_r = _safe_float(odds.get("sl_rate_pct"))
            to_r = _safe_float(odds.get("timeout_rate_pct"))
            adv_lines.append(
                f"  • Frekuensi Historis TP vs SL (n={odds.get('n')}): "
                f"TP {_fmt_pct(tp_r)} | SL {_fmt_pct(sl_r)} | "
                f"Timeout {_fmt_pct(to_r)}"
            )
        if adv_lines:
            lines.append("\n=== SINYAL LANJUTAN & KONTEKS STATISTIK ===")
            lines.extend(adv_lines)

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

    def _set_plan_cards(self, levels: dict[str, str], pullback_sub: str | None = None) -> None:
        """Tampilkan kartu ringkasan rencana beli (Entry/SL/TP/Lot/Biaya/Risiko) di tab Cek."""
        get = levels.get
        entry_sub = pullback_sub if pullback_sub else f"pullback {get('pullback', '-')}"
        _set_kpi(self.pc_entry, get("entry", "-"), entry_sub, color=THEME["accent"])
        _set_kpi(self.pc_sl, get("sl", "-"), "batas rugi jika harga turun ke SL", color=THEME["red"])
        _set_kpi(self.pc_tp, get("tp", "-"), "target ambil untung (TP)", color=THEME["green"])
        _set_kpi(self.pc_lots, get("lots", "-"), "1 lot = 100 saham", color=THEME["fg"])
        _set_kpi(self.pc_cost, get("cost", "-"), "entry × lot × 100", color=THEME["fg"])
        _set_kpi(self.pc_risk, get("risk", "-"), "kehilangan maks bila SL tersentuh", color=THEME["amber"])

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

    def _cek_goto_sinyal(self) -> None:
        sym = self.var_cek_ticker.get().strip().upper().replace(".JK", "")
        if sym:
            self.var_sinyal_search.set(sym)
        self._goto_tab(2, self.refresh_sinyal)

    def _cek_goto_paket(self) -> None:
        self._goto_tab(3, self.refresh_paket)

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


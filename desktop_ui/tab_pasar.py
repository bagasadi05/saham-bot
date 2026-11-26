"""Tab 1: Pasar & Rezim IHSG, breadth, rotasi sektor, asing, dan berita pasar."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import tkinter as tk
from tkinter import scrolledtext, ttk

from core import desktop_monitor as dm
from core.desktop_helpers import (
    _fmt_arrow_pct,
    _fmt_num,
    _fmt_pct,
    _news_item_tag,
    _safe_float,
    hit_test_rects,
    match_sector_option,
)
from desktop_ui.widgets import (
    FONT_MONO_BOLD,
    FONT_SMALL,
    FONT_SMALL_BOLD,
    FONT_UI,
    THEME,
    _make_kpi_card,
    _set_kpi,
)


class PasarTabMixin:
    def _build_market_tab(self) -> None:
        ctrl = tk.Frame(self.tab_market, bg=THEME["bg"], padx=8, pady=8)
        ctrl.pack(fill="x")
        tk.Label(
            ctrl,
            text="Ringkasan Kondisi Pasar BEI, Rezim IHSG, Breadth /scan, Asing & Sentimen",
            bg=THEME["bg"], fg=THEME["muted"], font=FONT_SMALL_BOLD,
        ).pack(side="left")
        # Lompat cepat ke fitur terkait (semua fitur saling terhubung)
        nav = tk.Frame(ctrl, bg=THEME["bg"])
        nav.pack(side="right", padx=(0, 8))
        for label, idx, fn in (
            ("⭐ Watchlist", 1, self.refresh_watch),
            ("🎯 Sinyal", 2, self.refresh_sinyal),
            ("📦 Paket", 3, self.refresh_paket),
            ("💼 Portofolio", 4, self.refresh_port),
        ):
            ttk.Button(
                nav, text=label, style="Ghost.TButton",
                command=lambda i=idx, f=fn: self._goto_tab(i, f),
            ).pack(side="left", padx=(0, 4))
        btn_market = ttk.Button(
            ctrl, text="↻ Segarkan Pasar & IDX", style="Accent.TButton",
            command=lambda: self.refresh_market(force=True),
        )
        btn_market.pack(side="right")
        self._reg_busy("market", btn_market)

        kpi_row = tk.Frame(self.tab_market, bg=THEME["bg"], padx=8)
        kpi_row.pack(fill="x", pady=(0, 8))
        for c in range(5):
            kpi_row.columnconfigure(c, weight=1, uniform="mkpi")

        self.mk_ihsg = _make_kpi_card(kpi_row, "IHSG (^JKSE)")
        self.mk_regime = _make_kpi_card(kpi_row, "Rezim & Skala")
        self.mk_breadth = _make_kpi_card(kpi_row, "Breadth /scan")
        self.mk_foreign = _make_kpi_card(kpi_row, "Aliran Asing BEI")
        self.mk_cond = _make_kpi_card(kpi_row, "Kondisi & Kesegaran")
        for idx, card in enumerate(
            (self.mk_ihsg, self.mk_regime, self.mk_breadth, self.mk_foreign, self.mk_cond)
        ):
            card["frame"].grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0))

        body = tk.PanedWindow(
            self.tab_market, orient="horizontal", bg=THEME["bg"], sashwidth=5, bd=0,
        )
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=10, pady=8,
        )
        tk.Label(
            left, text="ROTASI SEKTOR BEI (IDX-IC 1 HARI) — klik bar untuk filter Sinyal Pasar",
            bg=THEME["card"], fg=THEME["accent"], font=FONT_SMALL_BOLD, anchor="w",
        ).pack(fill="x", pady=(0, 6))
        self.canvas_sectors = tk.Canvas(
            left, bg=THEME["bg_elev"], highlightthickness=0, bd=0,
        )
        self.canvas_sectors.pack(fill="both", expand=True)
        self.canvas_sectors.bind("<Configure>", lambda _e: self._draw_sector_bars())
        # Klik bar sektor -> lompat ke Sinyal Pasar dengan filter sektor tersebut
        self.canvas_sectors.bind("<Button-1>", self._on_sector_click)
        self.canvas_sectors.bind("<Motion>", self._on_sector_motion)
        body.add(left, stretch="always", minsize=420)

        right = tk.Frame(
            body, bg=THEME["card"], highlightbackground=THEME["border"],
            highlightthickness=1, padx=10, pady=8,
        )
        tk.Label(
            right, text="DIAGNOSTIK PASAR & SENTIMEN BERITA", bg=THEME["card"],
            fg=THEME["accent"], font=FONT_SMALL_BOLD, anchor="w",
        ).pack(fill="x", pady=(0, 6))
        self.txt_market = scrolledtext.ScrolledText(
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
        self.txt_market.pack(fill="both", expand=True)
        self.txt_market.configure(state="disabled")
        body.add(right, stretch="always", minsize=400)

    def refresh_market(self, force: bool = True) -> None:
        if not force and self._last_market_pulse:
            return
        self.var_status.set("Mengambil data IHSG, breadth /scan, sektor BEI & berita pasar...")
        self._run_async(
            "market",
            lambda: dm.fetch_market_pulse(include_idx=True, include_news=True),
            "market",
        )

    def _apply_market(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            data = {}
        self._last_market_pulse = data
        ihsg = data.get("ihsg") or {}
        reg = ihsg.get("regime") or {}
        breadth = data.get("breadth") or {}
        foreign = data.get("foreign") or {}
        news = data.get("news") or {}

        close_val = _safe_float(ihsg.get("close"))
        ret1d = _safe_float(ihsg.get("ret1d"))
        ret20d = _safe_float(ihsg.get("ret20d"))
        ihsg_clr = THEME["green"] if (ret1d or 0) > 0 else (THEME["red"] if (ret1d or 0) < 0 else THEME["fg"])
        _set_kpi(
            self.mk_ihsg,
            _fmt_num(close_val, 1) if close_val else "tidak tersedia",
            f"1H {_fmt_arrow_pct(ret1d)} · 20H {_fmt_pct(ret20d)}",
            color=ihsg_clr,
        )

        reg_label = str(reg.get("label") or "TIDAK TERSEDIA")
        scale = _safe_float(reg.get("exposure_scale"), 1.0) or 1.0
        reg_clr = THEME["green"] if scale >= 0.99 else (THEME["amber"] if scale >= 0.45 else THEME["red"])
        _set_kpi(
            self.mk_regime,
            f"{reg_label} ({scale:.1f}x)",
            str(reg.get("reason") or reg.get("summary") or "SMA50 vs SMA200 ^JKSE"),
            color=reg_clr,
        )
        self.var_regime_badge.set(
            f"IHSG {_fmt_num(close_val, 0)} ({_fmt_arrow_pct(ret1d)}) · Rezim {reg_label} ({scale:.1f}x)"
        )
        self.lbl_regime.configure(fg=reg_clr)

        if breadth.get("ok"):
            adv = breadth.get("advancing", 0)
            dec = breadth.get("declining", 0)
            beli = breadth.get("beli", 0)
            tot = breadth.get("total", 0)
            b_pct = _safe_float(breadth.get("beli_pct"))
            _set_kpi(
                self.mk_breadth,
                f"▲{adv} / ▼{dec}",
                f"BELI {beli}/{tot} ({b_pct:.1f}%)" if b_pct is not None else f"{tot} emiten",
                color=THEME["green"] if adv >= dec else THEME["red"],
            )
        else:
            _set_kpi(
                self.mk_breadth, "tidak tersedia", str(breadth.get("reason") or "belum /scan"),
                color=THEME["muted"],
            )

        if foreign.get("ok"):
            net_v = _safe_float(foreign.get("net_value"), 0.0) or 0.0
            f_clr = THEME["green"] if net_v > 0 else (THEME["red"] if net_v < 0 else THEME["fg"])
            _set_kpi(
                self.mk_foreign,
                str(foreign.get("label") or "-"),
                f"{foreign.get('n_rows', 0)} emiten BEI tercatat",
                color=f_clr,
            )
        else:
            _set_kpi(
                self.mk_foreign,
                "tidak tersedia",
                str(foreign.get("reason") or "data asing BEI offline"),
                color=THEME["muted"],
            )

        cond = str(data.get("condition") or "-")
        stale_txt = str(data.get("index_stale_age") or data.get("as_of_label") or "-")
        cond_clr = THEME["green"] if "KONDUSIF" in cond.upper() else (
            THEME["red"] if "WASPADA" in cond.upper() or "BERAT" in cond.upper() else THEME["amber"]
        )
        _set_kpi(
            self.mk_cond,
            cond,
            f"As of: {stale_txt}",
            color=cond_clr,
        )

        self._draw_sector_bars()

        lines = [
            f"KONDISI PASAR: {cond} ({data.get('as_of_label') or '-'})",
            f"Catatan Delay: {data.get('delay_note') or '-'}",
            "",
        ]
        for n in data.get("condition_notes") or []:
            lines.append(f"• {n}")
        lines.append("")
        if news and news.get("n"):
            npct = _safe_float(news.get("pct"), 0.0) or 0.0
            lines.append(
                f"SENTIMEN BERITA PASAR: {news.get('label', '-')} "
                f"(skor {npct:+.0f}%, {news.get('n', 0)} berita)"
            )
            for item in (news.get("details") or [])[:8]:
                title = item.get("title") or ""
                st = _news_item_tag(item)
                lines.append(f"  - [{st}] {title}")
        else:
            lines.append("SENTIMEN BERITA PASAR: tidak tersedia / RSS berita tidak terjangkau.")

        self._set_rich_text(self.txt_market, "\n".join(lines))
        self.var_status.set(
            f"Pasar diperbarui ({datetime.now().strftime('%H:%M:%S')} WIB) · Rezim {reg_label}"
        )

    def _draw_sector_bars(self) -> None:
        cv = self.canvas_sectors
        cv.delete("all")
        self._sector_rects = []
        w = max(360, cv.winfo_width())
        h = max(260, cv.winfo_height())

        sec_info = (self._last_market_pulse or {}).get("sectors") or {}
        if not sec_info.get("ok"):
            msg = sec_info.get("reason") or "Data indeks sektor BEI tidak tersedia (atau diblokir IP)."
            cv.create_text(
                w // 2, h // 2, text=msg, fill=THEME["muted"], font=FONT_SMALL, width=w - 40,
            )
            return

        rows = list(sec_info.get("sectors") or [])
        if not rows:
            cv.create_text(
                w // 2, h // 2, text="Tidak ada baris sektor IDX-IC.", fill=THEME["muted"],
                font=FONT_SMALL,
            )
            return

        max_abs = max(max(abs(_safe_float(r.get("pct"), 0.0) or 0.0) for r in rows), 0.5)
        n = len(rows)
        top_pad = 14
        row_h = min(30, max(20, (h - top_pad * 2) // max(n, 1)))
        label_w = 158
        pct_w = 68
        chart_left = label_w + 10
        chart_right = w - pct_w - 14
        center_x = (chart_left + chart_right) / 2.0
        half_span = max(30.0, (chart_right - chart_left) / 2.0 - 4.0)

        cv.create_line(center_x, top_pad - 4, center_x, top_pad + n * row_h, fill=THEME["border"], width=1)
        for i, row in enumerate(rows):
            y0 = top_pad + i * row_h
            ym = y0 + row_h / 2.0
            pct = _safe_float(row.get("pct"), 0.0) or 0.0
            full_name = str(row.get("name") or row.get("code") or "-")
            name = full_name[:22]
            clr = THEME["green"] if pct > 0 else (THEME["red"] if pct < 0 else THEME["muted"])

            cv.create_text(8, ym, text=name, anchor="w", fill=THEME["fg"], font=FONT_SMALL)
            bar_len = (abs(pct) / max_abs) * half_span
            if pct >= 0:
                x0, x1 = center_x, center_x + max(2.0, bar_len)
            else:
                x0, x1 = center_x - max(2.0, bar_len), center_x
            cv.create_rectangle(x0, y0 + 5, x1, y0 + row_h - 5, fill=clr, outline="")
            cv.create_text(
                w - 10, ym, text=f"{pct:+.2f}%", anchor="e", fill=clr, font=FONT_MONO_BOLD,
            )
            # rect area klik (label + bar + pct) untuk lompat ke filter sinyal
            self._sector_rects.append((0.0, float(y0), float(w), float(y0 + row_h), full_name))

    def _on_sector_motion(self, event: tk.Event) -> None:
        hit = hit_test_rects(self._sector_rects, event.x, event.y)
        try:
            self.canvas_sectors.configure(cursor="hand2" if hit else "")
        except Exception:
            pass

    def _on_sector_click(self, event: tk.Event) -> None:
        name = hit_test_rects(self._sector_rects, event.x, event.y)
        if not name:
            return
        options = [str(v) for v in (self.cb_sinyal_sector.cget("values") or [])]
        match = match_sector_option(name, options)
        if match and match != "Semua Sektor":
            self.var_sinyal_sector.set(match)
            self._goto_tab(2, self.refresh_sinyal)
            self.var_status.set(
                f"Sinyal Pasar difilter sektor '{match}' (dari grafik rotasi). "
                "Pilih 'Semua Sektor' untuk reset."
            )
        else:
            self.var_status.set(
                f"Sektor '{name}' belum ada di snapshot /scan — jalankan ⚡ Scan BEI lebih dulu."
            )


"""Daftar & penyaring saham syariah BEI (ISSI / DES OJK).

Sumber acuan: konstituen Indeks Saham Syariah Indonesia (ISSI) sesuai
Daftar Efek Syariah (DES) OJK yang disimpan di ``data/idx_syariah.json``.
Review DES dilakukan OJK setiap Mei & November, sehingga pengguna tetap
disarankan memverifikasi ulang pada daftar DES OJK / aplikasi sekuritas
syariah sebelum transaksi.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from html import escape as esc

log = logging.getLogger("sahambot.syariah")

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "idx_syariah.json"

_DEFAULT_LIQUID_SEED = (
    "TLKM", "ASII", "BRIS", "ICBP", "INDF", "ADRO", "AADI", "ADMR",
    "ANTM", "INCO", "UNTR", "UNVR", "KLBF", "CPIN", "JPFA", "PTBA",
    "ITMG", "PGAS", "MEDC", "AKRA", "MDKA", "BRPT", "TPIA", "ISAT",
    "EXCL", "JSMR", "BSDE", "CTRA", "PWON", "MYOR",
)


def normalize_code(ticker: str | None) -> str:
    """Normalisasi kode emiten BEI menjadi 4 huruf kapital tanpa sufiks `.JK`."""
    raw = str(ticker or "").strip().upper()
    if raw.endswith(".JK"):
        raw = raw[:-3]
    return raw


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    try:
        if _DATA_FILE.exists():
            data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("tickers"), list):
                return data
    except Exception:
        log.exception("gagal memuat %s", _DATA_FILE)
    return {
        "source": "BEI / OJK DES (fallback)",
        "announcement": "-",
        "effective_date": "-",
        "review_schedule": "Mei dan November",
        "count": len(_DEFAULT_LIQUID_SEED),
        "liquid_seed": list(_DEFAULT_LIQUID_SEED),
        "tickers": list(_DEFAULT_LIQUID_SEED),
    }


def reload_cache() -> None:
    """Bersihkan cache internal (berguna untuk pengujian / update file)."""
    _load_raw.cache_clear()
    syariah_tickers.cache_clear()


@lru_cache(maxsize=1)
def syariah_tickers() -> frozenset[str]:
    """Himpunan kode 4-huruf (tanpa `.JK`) seluruh saham syariah ISSI."""
    raw = _load_raw()
    return frozenset(
        normalize_code(t)
        for t in (raw.get("tickers") or [])
        if normalize_code(t)
    )


def metadata() -> dict:
    """Ringkasan metadata sumber daftar saham syariah."""
    raw = _load_raw()
    return {
        "source": str(raw.get("source") or "BEI / OJK DES (ISSI)"),
        "announcement": str(raw.get("announcement") or "-"),
        "effective_date": str(raw.get("effective_date") or "-"),
        "review_schedule": str(raw.get("review_schedule") or "Mei & November"),
        "count": len(syariah_tickers()),
    }


def is_syariah(ticker: str | None) -> bool:
    """True jika emiten termasuk konstituen ISSI / DES OJK lokal."""
    code = normalize_code(ticker)
    return bool(code) and code in syariah_tickers()


def syariah_badge(ticker: str | None) -> str:
    """Label singkat untuk header laporan bila emiten termasuk saham syariah."""
    return "☪️ Syariah" if is_syariah(ticker) else ""


def filter_tickers(
    tickers: Iterable[str],
    *,
    syariah_only: bool = True,
) -> list[str]:
    """Saring daftar kode saham; pertahankan format asli (dengan/tanpa `.JK`)."""
    items = list(tickers)
    if not syariah_only:
        return items
    allowed = syariah_tickers()
    return [t for t in items if normalize_code(t) in allowed]


def filter_rows(
    rows: list[dict],
    *,
    syariah_only: bool = True,
) -> list[dict]:
    """Saring baris snapshot (`/sinyal`, `/paket`) berdasarkan status syariah."""
    if not syariah_only:
        return list(rows)
    allowed = syariah_tickers()
    return [r for r in rows if normalize_code(r.get("ticker")) in allowed]


def syariah_universe(limit: int = 30) -> list[str]:
    """Daftar emiten syariah likuid (format `.JK`) sebagai seed `/saran`."""
    raw = _load_raw()
    seed = raw.get("liquid_seed") or list(_DEFAULT_LIQUID_SEED)
    out = [f"{normalize_code(t)}.JK" for t in seed if normalize_code(t)]
    return out[: max(1, int(limit))]


def format_check_text(ticker: str) -> str:
    """Pesan HTML status syariah untuk `/syariah cek <TICKER>`."""
    code = normalize_code(ticker)
    meta = metadata()
    if not code or not code.isalnum() or not (1 <= len(code) <= 10):
        return (
            "❌ Kode saham tidak valid. Contoh: <code>/syariah cek TLKM</code> "
            "atau <code>/syariah cek BBCA</code>."
        )
    if is_syariah(code):
        return (
            f"☪️ <b>{esc(code)}</b> — <b>TERMASUK SAHAM SYARIAH (ISSI)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• Status: Tercatat di daftar ISSI / DES OJK lokal ({meta['count']} emiten)\n"
            f"• Acuan: <code>{esc(meta['announcement'])}</code> (efektif {esc(meta['effective_date'])})\n\n"
            "<blockquote>ℹ️ Review DES OJK dilakukan tiap Mei &amp; November. "
            "Selalu cek ulang status syariah terbaru di aplikasi sekuritas syariahmu.</blockquote>"
        )
    return (
        f"🌐 <b>{esc(code)}</b> — <b>BUKAN SAHAM SYARIAH (Non-ISSI)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Status: Tidak tercatat di daftar ISSI / DES OJK lokal ({meta['count']} emiten)\n"
        f"• Acuan: <code>{esc(meta['announcement'])}</code> (efektif {esc(meta['effective_date'])})\n\n"
        "<blockquote>ℹ️ Emiten perbankan konvensional, pembiayaan berbunga, rokok, "
        "atau yang rasio utang berbasis bunganya melewati batas OJK tidak masuk DES. "
        "Cek ulang ke DES OJK terbaru bila emiten baru IPO.</blockquote>"
    )

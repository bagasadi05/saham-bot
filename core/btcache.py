"""Cache ringkasan backtest 5 tahun per emiten untuk verdict /beli & /jual (P8).

Disimpan sebagai JSON di folder sementara (`TMP_DIR`) dengan batas umur 1 hari
(86.400 detik) supaya `/beli` dan `/jual` tetap instan (<10 detik) tanpa
menghitung ulang `backtesting.py` inline.
"""
from __future__ import annotations

import html
import json
import os
import time

from core import data as datamod

BT_CACHE_TTL = 24 * 3600  # maks. 1 hari


def _symbol_key(ticker: str) -> str:
    try:
        return datamod.normalize_ticker(ticker).replace(".JK", "")
    except ValueError:
        return str(ticker).strip().upper().replace(".JK", "")


def _cache_path(cache_dir: str, ticker: str) -> str:
    return os.path.join(cache_dir, f"bt_{_symbol_key(ticker)}.json")


def save_summary(cache_dir: str, summary: dict) -> str | None:
    """Simpan ringkasan backtest secara atomik ke JSON."""
    if not cache_dir or not summary or not summary.get("ticker"):
        return None
    sym = _symbol_key(summary["ticker"])
    payload = dict(summary)
    payload["ticker"] = sym
    payload.setdefault("ts", time.time())
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = _cache_path(cache_dir, sym)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def load_summary(cache_dir: str, ticker: str,
                 max_age: float = BT_CACHE_TTL) -> dict | None:
    """Baca ringkasan backtest jika masih segar (<= max_age detik)."""
    if not cache_dir or max_age <= 0:
        return None
    path = _cache_path(cache_dir, ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        ts = float(payload.get("ts") or 0)
        if time.time() - ts > max_age:
            return None
        return payload
    except (OSError, ValueError, TypeError):
        return None


def format_verdict_backtest(summary: dict | None, ticker: str) -> str:
    """Satu baris ringkasan backtest untuk disisipkan ke verdict /beli /jual."""
    sym = html.escape(_symbol_key(ticker))
    if not summary:
        return (f"🧪 <i>Backtest 5 thn belum tersedia — jalankan "
                f"/backtest {sym} dulu.</i>")
    wr = f"{summary['win_rate']:.1f}%" if summary.get("win_rate") is not None else "—"
    pf = (f"{summary['profit_factor']:.2f}"
          if summary.get("profit_factor") is not None else "—")
    ret = f"{summary['ret_ann']:+.1f}%" if summary.get("ret_ann") is not None else "—"
    bh = f"{summary['bh_ann']:+.1f}%" if summary.get("bh_ann") is not None else "—"
    trades = int(summary.get("trades") or 0)
    as_of = html.escape(str(summary.get("as_of") or "—"))
    return (
        f"🧪 <b>Backtest 5 thn</b> (simulasi historis, bukan janji): "
        f"win-rate {wr}, PF {pf}, return {ret}/thn vs "
        f"buy&amp;hold {bh}/thn ({trades} transaksi, data {as_of})"
    )

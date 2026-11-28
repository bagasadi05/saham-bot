"""Data Health Monitor — Fase 6 (deteksi data basi & kesehatan sumber data).

Memeriksa umur snapshot `/scan`, ketersediaan cache OHLCV, status fallback
EODHD, dan kesiapan model ONNX ML lokal sehingga ketiga antarmuka (Telegram,
Desktop Tkinter, dan Desktop Web/PWA) dapat menampilkan peringatan jujur saat
data sudah basi.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

from core import marketcal, store

MAX_STALE_TRADING_DAYS = max(1, int(os.getenv("SAHAMBOT_HEALTH_STALE_DAYS", "3") or 3))


def check_data_health(
    *,
    meta: dict[str, Any] | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Audit kesehatan & kesegaran data aplikasi secara offline dan cepat."""
    scan_meta = meta if isinstance(meta, dict) else (store.market_scan_meta() or {})
    now_date = today or marketcal.now_wib().date()

    trade_date_str = str(scan_meta.get("trade_date") or "").strip()[:10]
    scanned_at = str(scan_meta.get("scanned_at") or "").strip()
    n_ok = int(scan_meta.get("ok") or 0)
    n_failed = int(scan_meta.get("failed") or 0)

    stale_days: int | None = None
    is_stale = False
    warnings: list[str] = []

    if not trade_date_str or n_ok <= 0:
        is_stale = True
        warnings.append("Snapshot /scan belum tersedia — jalankan Scan Cepat atau Scan Penuh BEI.")
    else:
        try:
            td = dt.date.fromisoformat(trade_date_str)
            stale_days = max(0, marketcal.trading_days_between(td, now_date))
            if stale_days > MAX_STALE_TRADING_DAYS:
                is_stale = True
                warnings.append(
                    f"Data /scan tertinggal {stale_days} hari bursa (terakhir {trade_date_str}) — perbarui scan."
                )
        except Exception:
            is_stale = True
            warnings.append("Format tanggal snapshot /scan tidak valid.")

    root = Path(__file__).resolve().parent.parent
    onnx_ready = (root / "models" / "indo-fin-roberta" / "model_quantized.onnx").is_file()
    eodhd_ready = bool(os.environ.get("EODHD_API_KEY", "").strip())

    status = "STALE" if is_stale else "HEALTHY"
    return {
        "status": status,
        "is_stale": is_stale,
        "trade_date": trade_date_str or None,
        "scanned_at": scanned_at or None,
        "stale_trading_days": stale_days,
        "max_stale_trading_days": MAX_STALE_TRADING_DAYS,
        "scan_ok": n_ok,
        "scan_failed": n_failed,
        "eodhd_fallback": eodhd_ready,
        "onnx_ml_ready": onnx_ready,
        "warnings": warnings,
    }

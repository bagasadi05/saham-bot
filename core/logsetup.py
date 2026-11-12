"""Konfigurasi logging: teks ringkas (default) atau JSON satu baris.

Aktifkan `LOG_JSON=1` bila log dibaca mesin (mis. `docker logs | jq`): tiap
kejadian jadi satu baris JSON dengan field tetap (ts, level, logger, msg) plus
field terstruktur yang dikenal (`cmd`, `chat_id`, `duration_ms`, `ok`) dan
`exc` bila ada traceback.

Tanpa env itu, format teks lama dipakai apa adanya supaya `bot.log` tetap
nyaman dibaca manusia.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

FIELDS = ("cmd", "chat_id", "duration_ms", "ok")
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_TRUE = ("1", "true", "yes", "on")


def json_enabled() -> bool:
    return os.getenv("LOG_JSON", "").strip().lower() in _TRUE


class JsonFormatter(logging.Formatter):
    """Satu baris JSON per record; traceback tetap disertakan sebagai string."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": dt.datetime.fromtimestamp(
                record.created, dt.timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> bool:
    """Pasang satu handler root; return True bila mode JSON aktif.

    Aman dipanggil sekali saat start. Handler lama dibuang supaya tidak ada
    baris ganda kalau modul di-import ulang (mis. di tes).
    """
    use_json = json_enabled()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if use_json
                         else logging.Formatter(TEXT_FORMAT))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    # [kendala 6] token Telegram tampil di URL httpx — jangan sampai ke log
    for noisy in ("httpx", "httpcore", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return use_json

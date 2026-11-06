"""Penyimpanan watchlist & alert per chat (JSON sederhana)."""
from __future__ import annotations

import json
import os
import threading

from config import STORE_FILE

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(STORE_FILE):
        return {}
    try:
        with open(STORE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    tmp = STORE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE_FILE)


def _chat(data: dict, chat_id: str) -> dict:
    return data.setdefault(str(chat_id), {"watchlist": [], "alerts": {}})


def get_watchlist(chat_id: int | str) -> list[str]:
    with _lock:
        return _chat(_load(), chat_id)["watchlist"]


def watchlist_add(chat_id: int | str, symbol: str) -> tuple[bool, list[str]]:
    """Return (berhasil, watchlist_terbaru). False kalau sudah ada."""
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        if symbol not in c["watchlist"]:
            c["watchlist"].append(symbol)
            _save(data)
            return True, c["watchlist"]
        return False, c["watchlist"]


def watchlist_remove(chat_id: int | str, symbol: str) -> tuple[bool, list[str]]:
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        if symbol in c["watchlist"]:
            c["watchlist"].remove(symbol)
            _save(data)
            return True, c["watchlist"]
        return False, c["watchlist"]


def set_alert(chat_id: int | str, symbol: str, kinds: set[str]) -> set[str]:
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        saved = set(c["alerts"].get(symbol) or set())
        saved |= set(kinds)
        c["alerts"][symbol] = sorted(saved)
        _save(data)
        return set(saved)


def remove_alert(chat_id: int | str, symbol: str) -> bool:
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        if symbol in c["alerts"]:
            del c["alerts"][symbol]
            _save(data)
            return True
        return False


def get_paper(chat_id: int | str) -> dict[str, dict]:
    with _lock:
        return _chat(_load(), chat_id).setdefault("paper", {})


def save_paper(chat_id: int | str, paper: dict[str, dict]):
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        c["paper"] = paper
        _save(data)


def get_alerts(chat_id: int | str) -> dict[str, list]:
    with _lock:
        return _chat(_load(), chat_id)["alerts"]


# [P5] modal latihan (paper trading) — default Rp10 juta, bisa diubah per chat
DEFAULT_MODAL = 10_000_000


def get_modal(chat_id: int | str) -> int:
    with _lock:
        return int(_chat(_load(), chat_id).get("modal", DEFAULT_MODAL))


def set_modal(chat_id: int | str, amount: int):
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        c["modal"] = int(amount)
        _save(data)


# [P6] auto-scan pagi per chat (default off)
def set_auto(chat_id: int | str, enabled: bool):
    with _lock:
        data = _load()
        c = _chat(data, chat_id)
        c["auto"] = bool(enabled)
        _save(data)


def get_auto_chats() -> list[str]:
    """Daftar chat_id (string) yang mengaktifkan auto-scan pagi."""
    out = []
    for chat_id, c in _load().items():
        if isinstance(c, dict) and c.get("auto"):
            out.append(chat_id)
    return out


def get_auto(chat_id: int | str) -> bool:
    with _lock:
        return bool(_load().get(str(chat_id), {}).get("auto", False))


class StoreWarning:
    """Catatan: _save menulis set sebagai list lazim, jadi tak ada tipe set mangling di JSON."""

"""Domain penyimpanan: mode pengguna, onboarding, pelacak tesis, dan state UI desktop."""
from __future__ import annotations

import json
import re

from core import store as _store

# ------------------------------------------- mode pengguna & onboarding -------
DEFAULT_USER_MODE = "pemula"
USER_MODES = ("pemula", "expert")
_USER_MODE_KEY = "user_mode"
_SYARIAH_MODE_KEY = "syariah_only"
_ONBOARD_STATE_KEY = "onboard_state"
_USER_MODE_ALIASES = {
    "pemula": "pemula",
    "beginner": "pemula",
    "newbie": "pemula",
    "expert": "expert",
    "pro": "expert",
    "mahir": "expert",
    "cepat": "expert",
}


def normalize_user_mode(raw: str | None) -> str:
    """Normalkan alias mode tampilan ('pemula' vs 'expert')."""
    if not isinstance(raw, str):
        raise ValueError(f"Mode tidak valid: {raw!r}. Pilih 'pemula' atau 'expert'.")
    token = raw.strip().lower()
    mode = _USER_MODE_ALIASES.get(token)
    if mode is None:
        raise ValueError(f"Mode tidak valid: {raw!r}. Pilih 'pemula' atau 'expert'.")
    return mode


def _read_chat_setting(conn, cid: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM chat_settings WHERE chat_id=? AND key=?",
        (cid, key),
    ).fetchone()
    return row["value"] if row else None


def _upsert_chat_setting(conn, cid: str, key: str, value: str) -> None:
    _store._ensure_chat(conn, cid)
    conn.execute(
        "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
        (cid, key, value),
    )


def _stored_user_mode(chat_id: int | str) -> str | None:
    with _store._db() as conn:
        raw = _read_chat_setting(conn, str(chat_id), _USER_MODE_KEY)
    if raw is None:
        return None
    try:
        return normalize_user_mode(raw)
    except ValueError:
        return None


def get_user_mode(chat_id: int | str) -> str:
    """Ambil mode tampilan chat ('pemula' atau 'expert'); fallback aman ke 'pemula'."""
    return _stored_user_mode(chat_id) or DEFAULT_USER_MODE


def has_user_mode(chat_id: int | str) -> bool:
    """True jika chat sudah menyimpan pilihan mode yang valid di chat_settings."""
    return _stored_user_mode(chat_id) is not None


def set_user_mode(chat_id: int | str, mode: str) -> str:
    """Validasi, simpan mode tampilan chat ('pemula'|'expert'), lalu kembalikan nilai kanonik."""
    norm = normalize_user_mode(mode)
    with _store._db() as conn:
        _upsert_chat_setting(conn, str(chat_id), _USER_MODE_KEY, norm)
    return norm


def get_syariah_mode(chat_id: int | str) -> bool:
    """True bila chat memilih filter Saham Syariah (ISSI/DES OJK) saja."""
    with _store._db() as conn:
        raw = _read_chat_setting(conn, str(chat_id), _SYARIAH_MODE_KEY)
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on", "syariah")


def set_syariah_mode(chat_id: int | str, enabled: bool) -> bool:
    """Simpan preferensi filter saham syariah per chat (True=Syariah, False=Semua)."""
    val = bool(enabled)
    with _store._db() as conn:
        _upsert_chat_setting(conn, str(chat_id), _SYARIAH_MODE_KEY, "1" if val else "0")
    return val


def _decode_onboard_state(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def get_onboard_state(chat_id: int | str) -> dict:
    """Ambil state onboarding linear chat (dict kosong bila belum ada atau rusak)."""
    with _store._db() as conn:
        raw = _read_chat_setting(conn, str(chat_id), _ONBOARD_STATE_KEY)
    return _decode_onboard_state(raw)


def set_onboard_state(chat_id: int | str, **updates) -> dict:
    """Gabungkan `updates` ke state onboarding chat; nilai None menghapus key."""
    cid = str(chat_id)
    with _store._db() as conn:
        state = _decode_onboard_state(_read_chat_setting(conn, cid, _ONBOARD_STATE_KEY))
        for k, v in updates.items():
            if v is None:
                state.pop(k, None)
            else:
                state[k] = v
        _upsert_chat_setting(
            conn,
            cid,
            _ONBOARD_STATE_KEY,
            json.dumps(state, ensure_ascii=False, default=str),
        )
    return state


def clear_onboard_state(chat_id: int | str) -> None:
    """Hapus state onboarding linear chat tanpa menyentuh pengaturan lain."""
    with _store._db() as conn:
        conn.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?",
            (str(chat_id), _ONBOARD_STATE_KEY),
        )


# ------------------------------------------- pelacak tesis posisi (W3) --------
_THESIS_KEY_PREFIX = "thesis_state:"


def _thesis_key(ticker: str) -> str:
    return f"{_THESIS_KEY_PREFIX}{str(ticker or '').strip().upper().removesuffix('.JK')}"


def _decode_thesis_state(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed.get("status"):
        return None
    return dict(parsed)


def get_position_thesis(chat_id: int | str, ticker: str) -> dict | None:
    """Ambil status tesis terakhir posisi (None bila belum tercatat / rusak)."""
    with _store._db() as conn:
        raw = _read_chat_setting(conn, str(chat_id), _thesis_key(ticker))
    return _decode_thesis_state(raw)


def set_position_thesis(
    chat_id: int | str,
    ticker: str,
    *,
    status: str,
    score: int = 0,
    reasons: list[str] | None = None,
    updated_at: str | None = None,
    alerted_status: str | None = None,
    preserve_alerted: bool = False,
) -> dict:
    """Simpan status tesis terkini untuk posisi `(chat_id, ticker)` di `chat_settings`.

    `preserve_alerted=True` dipakai jalur tampilan (`/portofolio`) agar status
    alert terakhir (`alerted_status`) yang dipakai `scan_alerts` tidak tertimpa
    sebelum notifikasi perubahan tesis benar-benar terkirim.
    """
    t = str(ticker or "").strip().upper().removesuffix(".JK")
    st = str(status).strip().upper()
    key = _thesis_key(t)
    with _store._db() as conn:
        if preserve_alerted:
            prev = _decode_thesis_state(_read_chat_setting(conn, str(chat_id), key))
            if prev is not None:
                eff_alerted = (
                    prev.get("alerted_status")
                    if "alerted_status" in prev
                    else prev.get("status")
                )
            else:
                eff_alerted = st if st in ("TETAP", "MENGUAT") else None
        elif alerted_status is not None:
            eff_alerted = str(alerted_status).strip().upper()
        else:
            eff_alerted = st

        payload = {
            "ticker": t,
            "status": st,
            "alerted_status": eff_alerted,
            "score": int(score),
            "reasons": [str(r) for r in (reasons or [])[:5]],
            "updated_at": updated_at or _store._stamp(),
        }
        _upsert_chat_setting(
            conn,
            str(chat_id),
            key,
            json.dumps(payload, ensure_ascii=False),
        )
    return payload


def clear_position_thesis(chat_id: int | str, ticker: str) -> None:
    """Hapus status tesis tersimpan untuk satu posisi."""
    with _store._db() as conn:
        conn.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?",
            (str(chat_id), _thesis_key(ticker)),
        )


def get_all_position_theses(chat_id: int | str) -> dict[str, dict]:
    """Ambil seluruh status tesis posisi tersimpan milik `chat_id`."""
    prefix = _THESIS_KEY_PREFIX
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM chat_settings WHERE chat_id=? AND key LIKE ?",
            (str(chat_id), f"{prefix}%"),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        dec = _decode_thesis_state(r["value"])
        if dec:
            sym = str(r["key"])[len(prefix):].upper()
            out[sym] = dec
    return out


# ------------------------------------------- state UI aplikasi desktop ---------
_DESKTOP_UI_STATE_KEY = "desktop_ui_state"

# Opsi auto-refresh yang dikenal aplikasi desktop (menit); "Manual" = 0.
DESKTOP_REFRESH_OPTIONS = {"Manual": 0, "5m": 5, "15m": 15, "30m": 30}
DESKTOP_TAB_MAX = 7  # 8 tab: indeks 0..7
FONT_SCALE_MIN = 0.8
FONT_SCALE_MAX = 1.5
RECENT_TICKERS_MAX = 12

DEFAULT_DESKTOP_UI_STATE: dict = {
    "geometry": "",
    "tab_index": 0,
    "refresh": "15m",
    "toast": True,
    "font_scale": 1.0,
    "recent_tickers": [],
}

_GEOMETRY_RE = re.compile(r"\d{3,5}x\d{3,5}\+\d{1,5}\+\d{1,5}")


def clamp_font_scale(value: object) -> float:
    """apitkan skala zoom font ke rentang aman [FONT_SCALE_MIN, FONT_SCALE_MAX]."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if v != v:  # NaN
        return 1.0
    return round(min(FONT_SCALE_MAX, max(FONT_SCALE_MIN, v)), 3)


def normalize_recent_tickers(raw: object, limit: int = RECENT_TICKERS_MAX) -> list[str]:
    """Normalkan daftar ticker riwayat cek: uppercase, tanpa sufiks .JK, ASCII-alnum, unik."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        t = str(item or "").strip().upper().removesuffix(".JK")
        t = "".join(ch for ch in t if ch.isascii() and ch.isalnum())
        if t and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def decode_ui_state(raw: str | None) -> dict:
    """Decode JSON state UI desktop menjadi dict aman; nilai invalid jatuh ke default."""
    state = dict(DEFAULT_DESKTOP_UI_STATE)
    state["recent_tickers"] = []
    if not raw:
        return state
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return state
    if not isinstance(parsed, dict):
        return state
    geo = str(parsed.get("geometry") or "")
    if _GEOMETRY_RE.fullmatch(geo):
        state["geometry"] = geo
    try:
        tab = int(parsed.get("tab_index", 0))
    except (TypeError, ValueError):
        tab = 0
    state["tab_index"] = max(0, min(DESKTOP_TAB_MAX, tab))
    refresh = str(parsed.get("refresh") or "")
    state["refresh"] = refresh if refresh in DESKTOP_REFRESH_OPTIONS else state["refresh"]
    state["toast"] = bool(parsed.get("toast", True))
    state["font_scale"] = clamp_font_scale(parsed.get("font_scale", 1.0))
    state["recent_tickers"] = normalize_recent_tickers(parsed.get("recent_tickers"))
    return state


def encode_ui_state(state: dict) -> str:
    """Sanitasi ``state`` via decode lalu kembalikan sebagai JSON string."""
    clean = decode_ui_state(json.dumps(state or {}, ensure_ascii=False))
    return json.dumps(clean, ensure_ascii=False)


def get_desktop_ui_state(chat_id: int | str) -> dict:
    """Ambil state UI desktop tersimpan (geometri, tab aktif, zoom, riwayat ticker, dst)."""
    with _store._db() as conn:
        raw = _read_chat_setting(conn, str(chat_id), _DESKTOP_UI_STATE_KEY)
    return decode_ui_state(raw)


def set_desktop_ui_state(chat_id: int | str, **updates: object) -> dict:
    """Gabungkan ``updates`` ke state tersimpan, simpan, dan kembalikan state baru."""
    current = get_desktop_ui_state(chat_id)
    current.update(updates)
    clean = decode_ui_state(json.dumps(current, ensure_ascii=False))
    with _store._db() as conn:
        _upsert_chat_setting(
            conn,
            str(chat_id),
            _DESKTOP_UI_STATE_KEY,
            json.dumps(clean, ensure_ascii=False),
        )
    return clean

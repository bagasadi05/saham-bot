"""Penyimpanan watchlist, alert, dan paper trading (SQLite).

Dulu JSON yang ditulis ulang penuh tiap perubahan: rawan lost update antara job
scanner dan handler, dan satu file rusak menghapus data semua user. SQLite
memberi transaksi atomik; `store.json` lama dimigrasikan otomatis sekali.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import STORE_DB, STORE_FILE

log = logging.getLogger("sahambuddy.store")

DEFAULT_MODAL = 10_000_000
KEEP_CLOSED = 20   # riwayat posisi latihan 'closed' yang disimpan per chat

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    modal   INTEGER NOT NULL DEFAULT 10000000,
    auto    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watchlist (
    chat_id TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    PRIMARY KEY (chat_id, symbol)
);
CREATE TABLE IF NOT EXISTS alerts (
    chat_id TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    kinds   TEXT NOT NULL DEFAULT '[]',
    active  TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (chat_id, symbol)
);
CREATE TABLE IF NOT EXISTS paper (
    chat_id TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    rec     TEXT NOT NULL,
    PRIMARY KEY (chat_id, symbol)
);
CREATE TABLE IF NOT EXISTS score_bands (
    chat_id TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    band    TEXT NOT NULL,
    PRIMARY KEY (chat_id, symbol)
);
CREATE TABLE IF NOT EXISTS div_reminders (
    chat_id  TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    ex_date  TEXT NOT NULL,
    days     INTEGER NOT NULL,
    PRIMARY KEY (chat_id, symbol, ex_date, days)
);
"""


_init_lock = threading.Lock()
_ready_for: str | None = None


def _ensure_ready(conn):
    """Buat skema + migrasi sekali per file DB, bukan pada tiap operasi.

    Dulu `executescript(_SCHEMA)` + cek file migrasi dijalankan pada setiap
    panggilan store — puluhan kali untuk satu `/saran`, tanpa manfaat.
    """
    global _ready_for
    if _ready_for == STORE_DB:
        return
    with _init_lock:
        if _ready_for == STORE_DB:
            return
        conn.executescript(_SCHEMA)
        _migrate_json(conn)
        _ready_for = STORE_DB


@contextmanager
def _db():
    """Koneksi + transaksi singkat (satu operasi tulis = satu transaksi)."""
    folder = os.path.dirname(STORE_DB)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(STORE_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        _ensure_ready(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_chat(conn, chat_id, modal: int | None = None, auto: bool | None = None):
    cid = str(chat_id)
    conn.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (cid,))
    if modal is not None:
        conn.execute("UPDATE chats SET modal=? WHERE chat_id=?", (int(modal), cid))
    if auto is not None:
        conn.execute("UPDATE chats SET auto=? WHERE chat_id=?", (int(bool(auto)), cid))


def _migrate_json(conn):
    """Impor store.json lama sekali, lalu ganti nama file agar tak diulang."""
    legacy = STORE_FILE
    if not legacy or not os.path.exists(legacy):
        return
    if conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]:
        return
    try:
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("migrasi: %s tidak bisa dibaca — dilewati", legacy)
        return
    if not isinstance(data, dict):
        return
    for chat_id, c in data.items():
        if not isinstance(c, dict):
            continue
        _ensure_chat(conn, chat_id,
                     modal=int(c.get("modal") or DEFAULT_MODAL),
                     auto=bool(c.get("auto")))
        for symbol in c.get("watchlist") or []:
            conn.execute("INSERT OR IGNORE INTO watchlist(chat_id, symbol) VALUES (?, ?)",
                         (str(chat_id), symbol))
        for symbol, kinds in (c.get("alerts") or {}).items():
            conn.execute(
                "INSERT OR REPLACE INTO alerts(chat_id, symbol, kinds, active) "
                "VALUES (?, ?, ?, '[]')",
                (str(chat_id), symbol, json.dumps(sorted(set(kinds or [])))))
        for symbol, rec in (c.get("paper") or {}).items():
            conn.execute(
                "INSERT OR REPLACE INTO paper(chat_id, symbol, rec) VALUES (?, ?, ?)",
                (str(chat_id), symbol, json.dumps(rec, ensure_ascii=False, default=str)))
    os.replace(legacy, legacy + ".migrated")
    log.info("migrasi store.json -> SQLite selesai (%d chat)", len(data))


# ------------------------------------------------------------ watchlist -----

def get_watchlist(chat_id: int | str) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE chat_id=? ORDER BY rowid",
            (str(chat_id),))
        return [r["symbol"] for r in rows]


def watchlist_add(chat_id: int | str, symbol: str) -> tuple[bool, list[str]]:
    """Return (berhasil, watchlist_terbaru). False kalau sudah ada."""
    with _db() as conn:
        _ensure_chat(conn, chat_id)
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol)).fetchone()
        if row is None:
            conn.execute("INSERT INTO watchlist(chat_id, symbol) VALUES (?, ?)",
                         (str(chat_id), symbol))
            added = True
        else:
            added = False
    return added, get_watchlist(chat_id)


def watchlist_remove(chat_id: int | str, symbol: str) -> tuple[bool, list[str]]:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol))
        removed = cur.rowcount > 0
    return removed, get_watchlist(chat_id)


# ---------------------------------------------------------------- alert -----

def set_alert(chat_id: int | str, symbol: str, kinds: set[str]) -> set[str]:
    with _db() as conn:
        _ensure_chat(conn, chat_id)
        row = conn.execute(
            "SELECT kinds FROM alerts WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol)).fetchone()
        saved = set(json.loads(row["kinds"])) if row else set()
        saved |= set(kinds)
        conn.execute(
            "INSERT INTO alerts(chat_id, symbol, kinds, active) VALUES (?, ?, ?, '[]') "
            "ON CONFLICT(chat_id, symbol) DO UPDATE SET kinds=excluded.kinds",
            (str(chat_id), symbol, json.dumps(sorted(saved))))
    return saved


def remove_alert(chat_id: int | str, symbol: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM alerts WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol))
        return cur.rowcount > 0


def get_alerts(chat_id: int | str) -> dict[str, list]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT symbol, kinds FROM alerts WHERE chat_id=? ORDER BY symbol",
            (str(chat_id),))
        return {r["symbol"]: json.loads(r["kinds"]) for r in rows}


def alert_subscriptions() -> list[tuple[str, str, set[str]]]:
    """Semua langganan alert: [(chat_id, symbol, kinds), ...]."""
    with _db() as conn:
        rows = conn.execute("SELECT chat_id, symbol, kinds FROM alerts")
        return [(r["chat_id"], r["symbol"], set(json.loads(r["kinds"]))) for r in rows]


def update_alert_state(chat_id: int | str, symbol: str, hits: set[str]) -> set[str]:
    """Simpan kondisi pemicu sekarang, kembalikan jenis yang BARU aktif.

    Scanner memakai ini supaya alert hanya dikirim saat kondisi berubah
    (False → True), bukan tiap 30 menit selama kondisi bertahan.
    """
    hits = set(hits)
    with _db() as conn:
        row = conn.execute(
            "SELECT active FROM alerts WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol)).fetchone()
        if row is None:
            return set()
        prev = set(json.loads(row["active"] or "[]"))
        conn.execute(
            "UPDATE alerts SET active=? WHERE chat_id=? AND symbol=?",
            (json.dumps(sorted(hits)), str(chat_id), symbol))
    return hits - prev


# -------------------------------------------------- [P10] proaktif ----------

def update_score_band(chat_id: int | str, symbol: str, band: str) -> str | None:
    """Simpan band PowerScore terakhir; return band lama hanya jika BERUBAH.

    Saat pertama kali diamati (belum ada catatan), baseline disimpan diam-diam
    dan fungsi mengembalikan None supaya tidak membanjiri chat.
    """
    cid = str(chat_id)
    with _db() as conn:
        _ensure_chat(conn, cid)
        row = conn.execute(
            "SELECT band FROM score_bands WHERE chat_id=? AND symbol=?",
            (cid, symbol)).fetchone()
        conn.execute(
            "INSERT INTO score_bands(chat_id, symbol, band) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, symbol) DO UPDATE SET band=excluded.band",
            (cid, symbol, band))
        if row is None or row["band"] == band:
            return None
        return str(row["band"])


def mark_div_reminder(chat_id: int | str, symbol: str,
                      ex_date: str, days_left: int) -> bool:
    """Catat pengingat dividen; True hanya jika pengingat ini baru pertama kali."""
    cid = str(chat_id)
    with _db() as conn:
        _ensure_chat(conn, cid)
        cur = conn.execute(
            "INSERT OR IGNORE INTO div_reminders(chat_id, symbol, ex_date, days) "
            "VALUES (?, ?, ?, ?)",
            (cid, symbol, ex_date, int(days_left)))
        return cur.rowcount > 0


# ---------------------------------------------------------------- paper -----

def get_paper(chat_id: int | str) -> dict[str, dict]:
    with _db() as conn:
        rows = conn.execute("SELECT symbol, rec FROM paper WHERE chat_id=?",
                            (str(chat_id),))
        return {r["symbol"]: json.loads(r["rec"]) for r in rows}


def set_paper_record(chat_id: int | str, symbol: str, rec: dict):
    with _db() as conn:
        _ensure_chat(conn, chat_id)
        conn.execute(
            "INSERT INTO paper(chat_id, symbol, rec) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, symbol) DO UPDATE SET rec=excluded.rec",
            (str(chat_id), symbol,
             json.dumps(rec, ensure_ascii=False, default=str)))


def delete_paper(chat_id: int | str, symbol: str):
    with _db() as conn:
        conn.execute("DELETE FROM paper WHERE chat_id=? AND symbol=?",
                     (str(chat_id), symbol))


def all_papers() -> list[tuple[str, str, dict]]:
    """Semua posisi latihan: [(chat_id, symbol, rec), ...]."""
    with _db() as conn:
        rows = conn.execute("SELECT chat_id, symbol, rec FROM paper")
        return [(r["chat_id"], r["symbol"], json.loads(r["rec"])) for r in rows]


def prune_paper(chat_id: int | str, keep_closed: int = KEEP_CLOSED) -> int:
    """Pangkas riwayat posisi latihan yang sudah selesai ('closed').

    Tabel paper dulu tak pernah dibersihkan: setelah berbulan-bulan, `/saran
    list` dan rekap PnL ikut membengkak tanpa batas. Sisakan `keep_closed`
    posisi selesai TERBARU (urut filled_at/queued); posisi open & pending
    tidak pernah disentuh. Return jumlah baris yang dihapus.
    """
    if keep_closed < 0:
        return 0
    with _db() as conn:
        rows = conn.execute("SELECT symbol, rec FROM paper WHERE chat_id=?",
                            (str(chat_id),)).fetchall()
        closed = []
        for r in rows:
            rec = json.loads(r["rec"])
            if rec.get("status") == "closed":
                stamp = str(rec.get("filled_at") or rec.get("queued") or "")
                closed.append((stamp, r["symbol"]))
        if len(closed) <= keep_closed:
            return 0
        closed.sort()  # terlama dulu
        drop = [sym for _, sym in closed[:len(closed) - keep_closed]]
        conn.executemany("DELETE FROM paper WHERE chat_id=? AND symbol=?",
                         [(str(chat_id), sym) for sym in drop])
        return len(drop)


# ------------------------------------------------------- modal & auto ------

def get_modal(chat_id: int | str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT modal FROM chats WHERE chat_id=?",
                           (str(chat_id),)).fetchone()
        return int(row["modal"]) if row else DEFAULT_MODAL


def set_modal(chat_id: int | str, amount: int):
    with _db() as conn:
        _ensure_chat(conn, chat_id, modal=int(amount))


def set_auto(chat_id: int | str, enabled: bool):
    with _db() as conn:
        _ensure_chat(conn, chat_id, auto=bool(enabled))


def get_auto(chat_id: int | str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT auto FROM chats WHERE chat_id=?",
                           (str(chat_id),)).fetchone()
        return bool(row["auto"]) if row else False


def get_auto_chats() -> list[str]:
    with _db() as conn:
        rows = conn.execute("SELECT chat_id FROM chats WHERE auto=1")
        return [r["chat_id"] for r in rows]


def counts() -> dict:
    """Ringkasan isi store untuk /status."""
    with _db() as conn:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])
        papers = [json.loads(r["rec"]) for r in conn.execute("SELECT rec FROM paper")]
        return {
            "chats": one("SELECT COUNT(*) FROM chats"),
            "watchlist": one("SELECT COUNT(*) FROM watchlist"),
            "alerts": one("SELECT COUNT(*) FROM alerts"),
            "paper_total": len(papers),
            "paper_open": sum(1 for p in papers
                              if p.get("status") in ("open", "pending")),
        }

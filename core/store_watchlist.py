"""Domain penyimpanan: watchlist, alert, notifikasi proaktif, zona beli, dan pengaturan modal/auto."""
from __future__ import annotations

import json

from core import store as _store

# ------------------------------------------------------------ watchlist -----

def get_watchlist(chat_id: int | str) -> list[str]:
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE chat_id=? ORDER BY rowid",
            (str(chat_id),))
        return [r["symbol"] for r in rows]


def watchlist_add(chat_id: int | str, symbol: str) -> tuple[bool, list[str]]:
    """Return (berhasil, watchlist_terbaru). False kalau sudah ada."""
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id)
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
    with _store._db() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol))
        removed = cur.rowcount > 0
    return removed, get_watchlist(chat_id)


# ---------------------------------------------------------------- alert -----

def set_alert(chat_id: int | str, symbol: str, kinds: set[str]) -> set[str]:
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id)
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
    with _store._db() as conn:
        cur = conn.execute(
            "DELETE FROM alerts WHERE chat_id=? AND symbol=?",
            (str(chat_id), symbol))
        return cur.rowcount > 0


def get_alerts(chat_id: int | str) -> dict[str, list]:
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT symbol, kinds FROM alerts WHERE chat_id=? ORDER BY symbol",
            (str(chat_id),))
        return {r["symbol"]: json.loads(r["kinds"]) for r in rows}


def alert_subscriptions() -> list[tuple[str, str, set[str]]]:
    """Semua langganan alert: [(chat_id, symbol, kinds), ...]."""
    with _store._db() as conn:
        rows = conn.execute("SELECT chat_id, symbol, kinds FROM alerts")
        return [(r["chat_id"], r["symbol"], set(json.loads(r["kinds"]))) for r in rows]


def update_alert_state(chat_id: int | str, symbol: str, hits: set[str]) -> set[str]:
    """Simpan kondisi pemicu sekarang, kembalikan jenis yang BARU aktif.

    Scanner memakai ini supaya alert hanya dikirim saat kondisi berubah
    (False → True), bukan tiap 30 menit selama kondisi bertahan.
    """
    hits = set(hits)
    with _store._db() as conn:
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
    with _store._db() as conn:
        _store._ensure_chat(conn, cid)
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


def has_div_reminder(chat_id: int | str, symbol: str,
                     ex_date: str, days_left: int) -> bool:
    """True bila pengingat (event, H-3/H-1) ini sudah pernah terkirim."""
    with _store._db() as conn:
        row = conn.execute(
            "SELECT 1 FROM div_reminders WHERE chat_id=? AND symbol=? "
            "AND ex_date=? AND days=?",
            (str(chat_id), symbol, ex_date, int(days_left))).fetchone()
        return row is not None


def mark_div_reminder(chat_id: int | str, symbol: str,
                      ex_date: str, days_left: int) -> bool:
    """Catat pengingat dividen; True hanya jika pengingat ini baru pertama kali."""
    cid = str(chat_id)
    with _store._db() as conn:
        _store._ensure_chat(conn, cid)
        cur = conn.execute(
            "INSERT OR IGNORE INTO div_reminders(chat_id, symbol, ex_date, days) "
            "VALUES (?, ?, ?, ?)",
            (cid, symbol, ex_date, int(days_left)))
        return cur.rowcount > 0



# ------------------------------------------------------- modal & auto ------

def get_modal(chat_id: int | str) -> int:
    with _store._db() as conn:
        row = conn.execute("SELECT modal FROM chats WHERE chat_id=?",
                           (str(chat_id),)).fetchone()
        return int(row["modal"]) if row else _store.DEFAULT_MODAL


def set_modal(chat_id: int | str, amount: int):
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id, modal=int(amount))


def set_auto(chat_id: int | str, enabled: bool):
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id, auto=bool(enabled))


def get_auto(chat_id: int | str) -> bool:
    with _store._db() as conn:
        row = conn.execute("SELECT auto FROM chats WHERE chat_id=?",
                           (str(chat_id),)).fetchone()
        return bool(row["auto"]) if row else False


def get_auto_chats() -> list[str]:
    with _store._db() as conn:
        rows = conn.execute("SELECT chat_id FROM chats WHERE auto=1")
        return [r["chat_id"] for r in rows]


def counts() -> dict:
    """Ringkasan isi store untuk /status."""
    with _store._db() as conn:
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
            "market_scan": one("SELECT COUNT(*) FROM market_scan"),
        }



# ------------------------------------------------- zona beli watchlist (P16)
def get_zone_states(chat_id: int | str) -> dict[str, str]:
    """Zona terakhir per emiten. Nilai "" berarti di luar zona."""
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT ticker, zone FROM zone_state WHERE chat_id=?",
            (str(chat_id),))
        return {str(r["ticker"]).upper(): str(r["zone"] or "") for r in rows}


def set_zone_state(chat_id: int | str, ticker: str, zone: str):
    """Simpan zona sekarang; "" menandai sudah keluar dari zona."""
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id)
        conn.execute(
            "INSERT INTO zone_state(chat_id, ticker, zone, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(chat_id, ticker) DO UPDATE SET"
            " zone=excluded.zone, updated_at=excluded.updated_at",
            (str(chat_id), str(ticker).upper(), str(zone or ""), _store._stamp()))



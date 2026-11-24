"""Domain penyimpanan: market_scan screener, histori sinyal, plan cache, dan backup SQLite.

Catatan keamanan SQL: f-string di modul ini hanya dipakai untuk nama kolom
(`_SCAN_COLS`, `_HISTORY_COLS`, `_SNAPSHOT_COLS`) yang konstanta hardcoded —
bukan input user. Semua nilai data SELALU lewat parameter `?` (parameterized),
sehingga tidak ada risiko SQL injection.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from core import store as _store

# ---------------------------------------------------------------- screener
# Hasil pemindaian seluruh pasar (lihat core/screener.py). Disimpan sebagai
# snapshot: satu baris per emiten, ditimpa atomik setiap scan selesai.
_SCAN_COLS = (
    "ticker", "name", "board", "trade_date", "price", "ret1d", "ret5d",
    "ret20d", "rsi", "macd_hist", "sma20", "sma50", "sma200", "atr",
    "vol_avg20", "value_avg20", "tech_score", "rel_pct", "signal", "reason",
    "entry", "sl", "tp",
)


def save_market_scan(rows: list[dict], meta: dict | None = None):
    """Ganti snapshot lama dengan hasil scan baru (atomik, satu transaksi)."""
    sql = (f"INSERT OR REPLACE INTO market_scan ({','.join(_SCAN_COLS)}) "
           f"VALUES ({','.join('?' * len(_SCAN_COLS))})")
    with _store._db() as conn:
        conn.execute("DELETE FROM market_scan")
        conn.executemany(sql, [tuple(r.get(c) for c in _SCAN_COLS)
                               for r in rows])
        if meta:
            for k, v in meta.items():
                conn.execute(
                    "INSERT INTO scan_meta(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(k), str(v)))


def load_market_scan(signal: str | None = None) -> list[dict]:
    """Snapshot hasil scan; `signal` opsional (BELI/JUAL/TAHAN)."""
    with _store._db() as conn:
        if signal:
            rows = conn.execute(
                "SELECT * FROM market_scan WHERE signal=? "
                "ORDER BY rel_pct DESC, ticker", (signal,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM market_scan "
                "ORDER BY CASE signal WHEN 'BELI' THEN 0 WHEN 'JUAL' THEN 1 "
                "ELSE 2 END, rel_pct DESC, ticker").fetchall()
        return [dict(r) for r in rows]


def market_scan_meta() -> dict:
    with _store._db() as conn:
        return {r["key"]: r["value"]
                for r in conn.execute("SELECT key, value FROM scan_meta")}


def market_scan_counts() -> dict:
    with _store._db() as conn:
        return {r["signal"]: int(r["n"]) for r in conn.execute(
            "SELECT signal, COUNT(*) AS n FROM market_scan GROUP BY signal")}


# ------------------------------------------------------- histori sinyal -----
# `market_scan` ditimpa setiap scan (satu snapshot). Tabel ini menyimpan satu
# baris per emiten per hari bursa supaya hasil sinyal bisa diukur belakangan:
# return setelah N hari, R-multiple, dan apakah SL/TP kena lebih dulu.
_HISTORY_COLS = ("trade_date", "ticker", "signal", "price", "entry", "sl",
                 "tp", "atr", "rel_pct", "reason")
HISTORY_KEEP_DAYS = 400


def append_scan_history(rows: list[dict], trade_date: str,
                        keep_days: int = HISTORY_KEEP_DAYS) -> int:
    """Simpan sinyal harian tanpa menghapus hari sebelumnya.

    Baris yang sudah ada pada tanggal+ticker sama diperbarui (scan ulang di hari
    yang sama), tetapi kolom hasil (`closed_at`, `fwd_ret`, ...) tidak disentuh.
    """
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = [
        (trade_date, str(r.get("ticker") or "").upper(), str(r.get("signal") or ""),
         r.get("price"), r.get("entry"), r.get("sl"), r.get("tp"), r.get("atr"),
         r.get("rel_pct"), r.get("reason"), now)
        for r in rows
        if r.get("ticker") and r.get("signal")
    ]
    if not payload:
        return 0
    updates = ", ".join(f"{c}=excluded.{c}" for c in
                        ("signal", "price", "entry", "sl", "tp", "atr",
                         "rel_pct", "reason"))
    sql = (f"INSERT INTO scan_history ({','.join(_HISTORY_COLS)}, recorded_at) "
           f"VALUES ({','.join('?' * (len(_HISTORY_COLS) + 1))}) "
           f"ON CONFLICT(trade_date, ticker) DO UPDATE SET {updates}")
    with _store._db() as conn:
        conn.executemany(sql, payload)
        try:
            cutoff = (dt.date.fromisoformat(trade_date)
                      - dt.timedelta(days=keep_days)).isoformat()
            conn.execute("DELETE FROM scan_history WHERE trade_date < ?", (cutoff,))
        except ValueError:
            pass  # tanggal aneh: jangan sampai hasil scan hilang karena prune
    return len(payload)


def pending_scan_history(until: str) -> list[dict]:
    """Baris yang belum punya hasil akhir dan tanggalnya <= `until`."""
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT trade_date, ticker, signal, price, entry, sl, tp "
            "FROM scan_history WHERE closed_at IS NULL AND trade_date <= ? "
            "ORDER BY trade_date, ticker", (until,)).fetchall()
        return [dict(r) for r in rows]


def close_scan_history(results: list[dict]) -> int:
    """Tandai hasil akhir baris histori (return %, R-multiple, outcome)."""
    if not results:
        return 0
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = [(now, r.get("bars"), r.get("fwd_ret"), r.get("r_multiple"),
                r.get("outcome"), r["trade_date"], r["ticker"])
               for r in results]
    with _store._db() as conn:
        conn.executemany(
            "UPDATE scan_history SET closed_at=?, bars=?, fwd_ret=?, "
            "r_multiple=?, outcome=? WHERE trade_date=? AND ticker=?", payload)
    return len(payload)


def closed_scan_history(since: str, signal: str | None = None) -> list[dict]:
    """Baris histori yang sudah punya hasil, sejak tanggal `since`."""
    sql = ("SELECT * FROM scan_history WHERE closed_at IS NOT NULL "
           "AND trade_date >= ?")
    params: list = [since]
    if signal:
        sql += " AND signal=?"
        params.append(signal)
    sql += " ORDER BY trade_date, ticker"
    with _store._db() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def scan_history_counts() -> dict:
    """Ringkasan histori untuk /jejak dan /status."""
    with _store._db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(closed_at IS NULL), 0) AS pending, "
            "MIN(trade_date) AS first, MAX(trade_date) AS last "
            "FROM scan_history").fetchone()
        return {"total": int(row["total"] or 0),
                "pending": int(row["pending"] or 0),
                "first": row["first"] or "",
                "last": row["last"] or ""}


def _rotate_backups(dest_dir: str, keep: int) -> list[str]:
    """Sisakan `keep` cadangan terbaru; return path yang dihapus."""
    files = sorted(
        (os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
         if f.startswith(_store.BACKUP_PREFIX) and f.endswith(".db")),
        key=lambda p: os.path.getmtime(p), reverse=True)
    removed = []
    for old in files[keep:]:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


def backup_to(dest_dir: str, keep: int = 7) -> str:
    """Cadangkan DB secara konsisten (aman WAL), cek integritas, lalu rotasi.

    `cp store.db` bukan backup saat WAL aktif; `Connection.backup` menyalin
    snapshot yang konsisten. File hasil dicek `PRAGMA integrity_check`.
    """
    keep = max(1, int(keep))
    os.makedirs(dest_dir, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = os.path.join(dest_dir, f"{_store.BACKUP_PREFIX}{stamp}.db")
    with _store._db() as conn:
        dst = sqlite3.connect(path)
        try:
            conn.backup(dst)
        finally:
            dst.close()
    check = sqlite3.connect(path)
    try:
        status = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if status != "ok":
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(f"backup rusak: {path} (integrity_check={status})")
    _rotate_backups(dest_dir, keep)
    return path


def save_plan_cache(cache_key: str, payload: dict, trade_date: str = ""):
    """Simpan hasil pengayaan rencana beli (global, bukan per chat)."""
    with _store._db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO plan_cache"
            "(cache_key, trade_date, created_at, payload) VALUES (?,?,?,?)",
            (str(cache_key), str(trade_date),
             dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             json.dumps(payload, ensure_ascii=False)))


def load_plan_cache(cache_key: str) -> dict | None:
    with _store._db() as conn:
        row = conn.execute(
            "SELECT payload FROM plan_cache WHERE cache_key=?",
            (str(cache_key),)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None




# ---------------------------------------------- snapshot harian (riset fitur)
_SNAPSHOT_COLS = (
    "trade_date", "ticker", "close", "volume",
    "foreign_buy", "foreign_sell", "foreign_net",
    "pe", "pbv", "roe", "der", "div_yield", "saved_at",
)


def save_daily_snapshots(rows: list[dict], trade_date: str) -> int:
    """Simpan snapshot harian (harga, aliran asing, fundamental) untuk riset point-in-time."""
    if not rows or not trade_date:
        return 0
    stamp = _store._stamp()
    written = 0
    with _store._db() as conn:
        for r in rows:
            t = str(r.get("ticker") or "").upper().replace(".JK", "").strip()
            if not t:
                continue
            vals = (
                str(trade_date),
                t,
                r.get("close"),
                r.get("volume"),
                r.get("foreign_buy"),
                r.get("foreign_sell"),
                r.get("foreign_net"),
                r.get("pe"),
                r.get("pbv"),
                r.get("roe"),
                r.get("der"),
                r.get("div_yield"),
                stamp,
            )
            conn.execute(
                "INSERT INTO daily_snapshots (" + ",".join(_SNAPSHOT_COLS) + ")"
                " VALUES (" + ",".join("?" * len(_SNAPSHOT_COLS)) + ")"
                " ON CONFLICT(trade_date, ticker) DO UPDATE SET "
                + ", ".join(f"{c}=excluded.{c}" for c in _SNAPSHOT_COLS[2:]),
                vals,
            )
            written += 1
    return written


def load_daily_snapshots(
    ticker: str | None = None,
    since: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Ambil histori snapshot harian (terbaru lebih dulu) untuk analisis faktor."""
    where, params = [], []
    if ticker:
        where.append("ticker=?")
        params.append(str(ticker).upper().replace(".JK", "").strip())
    if since:
        where.append("trade_date>=?")
        params.append(str(since))
    sql = "SELECT " + ",".join(_SNAPSHOT_COLS) + " FROM daily_snapshots"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, ticker ASC LIMIT ?"
    params.append(max(1, int(limit)))
    with _store._db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(zip(_SNAPSHOT_COLS, row, strict=True)) for row in rows]

"""Penyimpanan watchlist, alert, dan paper trading (SQLite).

Dulu JSON yang ditulis ulang penuh tiap perubahan: rawan lost update antara job
scanner dan handler, dan satu file rusak menghapus data semua user. SQLite
memberi transaksi atomik; `store.json` lama dimigrasikan otomatis sekali.
"""
from __future__ import annotations

import datetime as dt
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
CREATE TABLE IF NOT EXISTS market_scan (
    ticker     TEXT PRIMARY KEY,
    name       TEXT,
    board      TEXT,
    trade_date TEXT,
    price      REAL,
    ret1d      REAL,
    ret5d      REAL,
    ret20d     REAL,
    rsi        REAL,
    macd_hist  REAL,
    sma20      REAL,
    sma50      REAL,
    sma200     REAL,
    atr        REAL,
    vol_avg20  REAL,
    value_avg20 REAL,
    tech_score REAL,
    rel_pct    REAL,
    signal     TEXT NOT NULL,
    reason     TEXT,
    entry      REAL,
    sl         REAL,
    tp         REAL
);
CREATE TABLE IF NOT EXISTS scan_history (
    trade_date  TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    signal      TEXT NOT NULL,
    price       REAL,
    entry       REAL,
    sl          REAL,
    tp          REAL,
    atr         REAL,
    rel_pct     REAL,
    reason      TEXT,
    recorded_at TEXT NOT NULL,
    closed_at   TEXT,
    bars        INTEGER,
    fwd_ret     REAL,
    r_multiple  REAL,
    outcome     TEXT,
    PRIMARY KEY (trade_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_scan_history_open
    ON scan_history(trade_date) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS scan_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS plan_cache (
    cache_key  TEXT PRIMARY KEY,
    trade_date TEXT,
    created_at TEXT,
    payload    TEXT NOT NULL
);
-- P17: portofolio ASLI (uang sungguhan), terpisah total dari paper trading.
CREATE TABLE IF NOT EXISTS real_positions (
    chat_id    TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    lots       INTEGER NOT NULL,
    avg_price  REAL NOT NULL,
    sector     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, ticker)
);
CREATE TABLE IF NOT EXISTS real_trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    side       TEXT NOT NULL,
    lots       INTEGER NOT NULL,
    price      REAL NOT NULL,
    trade_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_real_trades_chat
    ON real_trades(chat_id, id DESC);
-- P16: zona beli terakhir per watchlist, supaya pesan hanya dikirim saat
-- status berubah (di luar zona -> di dalam zona), bukan tiap 30 menit.
CREATE TABLE IF NOT EXISTS zone_state (
    chat_id    TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    zone       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, ticker)
);
-- P28: konteks sinyal terakhir & jurnal eksekusi manual Stockbit (terpisah dari paper).
CREATE TABLE IF NOT EXISTS signal_context (
    chat_id        TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    source         TEXT NOT NULL,
    signal_price   REAL NOT NULL,
    sl             REAL,
    tp             REAL,
    suggested_lots INTEGER,
    trade_date     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    intent         TEXT NOT NULL DEFAULT 'beli',
    sell_payload   TEXT,
    PRIMARY KEY (chat_id, ticker)
);
CREATE TABLE IF NOT EXISTS real_executions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    side           TEXT NOT NULL,
    lots           INTEGER NOT NULL,
    exec_price     REAL NOT NULL,
    signal_price   REAL,
    sl             REAL,
    tp             REAL,
    source         TEXT NOT NULL DEFAULT 'manual',
    slippage_rp    REAL,
    slippage_pct   REAL,
    slippage_cost  REAL,
    avg_entry      REAL,
    gross_pnl      REAL,
    net_pnl        REAL,
    net_pnl_pct    REAL,
    trade_date     TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_real_exec_chat
    ON real_executions(chat_id, id DESC);
-- P23 lanjutan: pengaturan batas risiko per chat.
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""


_init_lock = threading.Lock()
_ready_for: str | None = None

SCHEMA_VERSION = 6          # naikkan + tambah langkah di _MIGRATIONS bila skema berubah
BACKUP_PREFIX = "store-"


def _user_version(conn) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _migrate_v2(conn):
    """v2: index parsial untuk baris histori sinyal yang belum ditutup."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_history_open "
        "ON scan_history(trade_date) WHERE closed_at IS NULL")


def _migrate_v3(conn):
    """v3: tabel portofolio asli (P17). Idempoten — aman dijalankan sekali."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS real_positions (
            chat_id    TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            lots       INTEGER NOT NULL,
            avg_price  REAL NOT NULL,
            sector     TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS real_trades (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            side       TEXT NOT NULL,
            lots       INTEGER NOT NULL,
            price      REAL NOT NULL,
            trade_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_real_trades_chat
            ON real_trades(chat_id, id DESC);
        """)


def _migrate_v4(conn):
    """v4: state zona beli watchlist (P16)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS zone_state (
            chat_id    TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            zone       TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, ticker)
        )
        """)


def _migrate_v5(conn):
    """v5: konteks sinyal & jurnal eksekusi manual Stockbit (P28)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS signal_context (
            chat_id        TEXT NOT NULL,
            ticker         TEXT NOT NULL,
            source         TEXT NOT NULL,
            signal_price   REAL NOT NULL,
            sl             REAL,
            tp             REAL,
            suggested_lots INTEGER,
            trade_date     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (chat_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS real_executions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id        TEXT NOT NULL,
            ticker         TEXT NOT NULL,
            side           TEXT NOT NULL,
            lots           INTEGER NOT NULL,
            exec_price     REAL NOT NULL,
            signal_price   REAL,
            sl             REAL,
            tp             REAL,
            source         TEXT NOT NULL DEFAULT 'manual',
            slippage_rp    REAL,
            slippage_pct   REAL,
            slippage_cost  REAL,
            avg_entry      REAL,
            gross_pnl      REAL,
            net_pnl        REAL,
            net_pnl_pct    REAL,
            trade_date     TEXT NOT NULL,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_real_exec_chat
            ON real_executions(chat_id, id DESC);
        """)


def _migrate_v6(conn):
    """v6: pengaturan batas risiko per chat (P23 lanjutan)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id TEXT NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT NOT NULL,
            PRIMARY KEY (chat_id, key)
        )
        """)


_MIGRATIONS = {
    2: _migrate_v2, 3: _migrate_v3, 4: _migrate_v4, 5: _migrate_v5, 6: _migrate_v6,
}


def _run_migrations(conn, current: int) -> int:
    """Jalankan langkah migrasi bernomor sampai SCHEMA_VERSION."""
    version = current
    for target in range(current + 1, SCHEMA_VERSION + 1):
        step = _MIGRATIONS.get(target)
        if step:
            step(conn)
        conn.execute(f"PRAGMA user_version = {int(target)}")
        version = target
    return version


def _migrate_scan_columns(conn):
    """Tambah kolom snapshot baru pada DB lama tanpa menghapus data."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(market_scan)")}
    for name, typ in (("vol_avg20", "REAL"), ("value_avg20", "REAL")):
        if name not in cols:
            conn.execute(f"ALTER TABLE market_scan ADD COLUMN {name} {typ}")


def _migrate_signal_context_columns(conn):
    """Tambah kolom intent & sell_payload pada signal_context DB v5/v6 lama."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(signal_context)")}
    if not cols:
        return
    if "intent" not in cols:
        conn.execute(
            "ALTER TABLE signal_context ADD COLUMN intent TEXT NOT NULL DEFAULT 'beli'"
        )
    if "sell_payload" not in cols:
        conn.execute("ALTER TABLE signal_context ADD COLUMN sell_payload TEXT")


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
        _migrate_scan_columns(conn)
        _migrate_signal_context_columns(conn)
        _migrate_json(conn)
        current = _user_version(conn)
        if current < SCHEMA_VERSION:
            _run_migrations(conn, current)
            log.info("skema store: v%s -> v%s", current, SCHEMA_VERSION)
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
            "market_scan": one("SELECT COUNT(*) FROM market_scan"),
        }


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
    with _db() as conn:
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
    with _db() as conn:
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
    with _db() as conn:
        return {r["key"]: r["value"]
                for r in conn.execute("SELECT key, value FROM scan_meta")}


def market_scan_counts() -> dict:
    with _db() as conn:
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
    with _db() as conn:
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
    with _db() as conn:
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
    with _db() as conn:
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
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def scan_history_counts() -> dict:
    """Ringkasan histori untuk /jejak dan /status."""
    with _db() as conn:
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
         if f.startswith(BACKUP_PREFIX) and f.endswith(".db")),
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
    path = os.path.join(dest_dir, f"{BACKUP_PREFIX}{stamp}.db")
    with _db() as conn:
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
        raise RuntimeError(f"backup rusak: {path} (integrity_check={status})")
    _rotate_backups(dest_dir, keep)
    return path


def save_plan_cache(cache_key: str, payload: dict, trade_date: str = ""):
    """Simpan hasil pengayaan rencana beli (global, bukan per chat)."""
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO plan_cache"
            "(cache_key, trade_date, created_at, payload) VALUES (?,?,?,?)",
            (str(cache_key), str(trade_date),
             dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             json.dumps(payload, ensure_ascii=False)))


def load_plan_cache(cache_key: str) -> dict | None:
    with _db() as conn:
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


# ------------------------------------------- portofolio ASLI / uang sungguhan
# P17: terpisah dari paper trading. Bot tidak pernah menaruh order — tabel ini
# hanya mencatat apa yang sudah dibeli/dijual pemilik di Stockbit.
def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_lot_price(lots, price) -> tuple[int, float]:
    try:
        n = int(lots)
        p = float(price)
    except (TypeError, ValueError):
        raise ValueError("lot dan harga harus berupa angka") from None
    if n <= 0:
        raise ValueError("lot harus lebih dari 0")
    if p <= 0:
        raise ValueError("harga harus lebih dari 0")
    return n, p


def _add_real_position_conn(conn, chat_id: int | str, t: str, n: int, p: float,
                            trade_date: str, sector: str | None,
                            now: str) -> dict:
    _ensure_chat(conn, chat_id)
    row = conn.execute(
        "SELECT lots, avg_price, sector FROM real_positions"
        " WHERE chat_id=? AND ticker=?", (str(chat_id), t)).fetchone()
    if row:
        old_lots, old_avg = int(row["lots"]), float(row["avg_price"])
        new_lots = old_lots + n
        new_avg = (old_lots * old_avg + n * p) / new_lots
        conn.execute(
            "UPDATE real_positions SET lots=?, avg_price=?, "
            "sector=COALESCE(?, sector), updated_at=?"
            " WHERE chat_id=? AND ticker=?",
            (new_lots, new_avg, sector, now, str(chat_id), t))
    else:
        new_lots, new_avg = n, p
        conn.execute(
            "INSERT INTO real_positions"
            "(chat_id, ticker, lots, avg_price, sector, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (str(chat_id), t, n, p, sector, now))
    conn.execute(
        "INSERT INTO real_trades"
        "(chat_id, ticker, side, lots, price, trade_date, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(chat_id), t, "BELI", n, p, str(trade_date), now))
    return {"ticker": t, "lots": new_lots, "avg_price": round(new_avg, 4)}


def _reduce_real_position_conn(conn, chat_id: int | str, t: str, n: int, p: float,
                               trade_date: str, now: str) -> dict:
    row = conn.execute(
        "SELECT lots, avg_price FROM real_positions"
        " WHERE chat_id=? AND ticker=?", (str(chat_id), t)).fetchone()
    if not row:
        raise ValueError(f"tidak ada posisi {t} di portofolio asli")
    held, avg = int(row["lots"]), float(row["avg_price"])
    if n > held:
        raise ValueError(f"kamu hanya punya {held} lot {t}")
    left = held - n
    if left:
        conn.execute(
            "UPDATE real_positions SET lots=?, updated_at=?"
            " WHERE chat_id=? AND ticker=?",
            (left, now, str(chat_id), t))
    else:
        conn.execute(
            "DELETE FROM real_positions WHERE chat_id=? AND ticker=?",
            (str(chat_id), t))
    conn.execute(
        "INSERT INTO real_trades"
        "(chat_id, ticker, side, lots, price, trade_date, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(chat_id), t, "JUAL", n, p, str(trade_date), now))
    return {"ticker": t, "lots_left": left, "avg_price": round(avg, 4),
            "avg_raw": avg, "sold_lots": n,
            "realized": round((p - avg) * n * 100)}


def _record_buy_ledger_conn(
    conn,
    chat_id: int | str,
    t: str,
    n: int,
    p: float,
    trade_date: str,
    now: str,
    *,
    signal_price: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "manual",
    sector: str | None = None,
    clear_context: bool = False,
) -> dict:
    """Sinkronkan real_positions, real_trades, dan real_executions untuk BELI."""
    from core import jurnal

    pos = _add_real_position_conn(conn, chat_id, t, n, p, trade_date, sector, now)
    slip_rp, slip_pct, slip_cost = jurnal.calc_slippage("BELI", p, signal_price, n)
    sig_val = float(signal_price) if signal_price and float(signal_price) > 0 else None
    sl_val = float(sl) if sl and float(sl) > 0 else None
    tp_val = float(tp) if tp and float(tp) > 0 else None
    src = str(source or "manual")
    conn.execute(
        "INSERT INTO real_executions"
        "(chat_id, ticker, side, lots, exec_price, signal_price, sl, tp,"
        " source, slippage_rp, slippage_pct, slippage_cost,"
        " trade_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(chat_id), t, "BELI", n, p, sig_val, sl_val, tp_val,
         src, slip_rp, slip_pct, slip_cost, str(trade_date), now))
    if clear_context:
        conn.execute(
            "DELETE FROM signal_context WHERE chat_id=? AND ticker=?",
            (str(chat_id), t))
    return {
        **pos, "exec_price": p, "bought_lots": n, "signal_price": sig_val,
        "sl": sl_val, "tp": tp_val,
        "slippage_rp": slip_rp, "slippage_pct": slip_pct,
        "slippage_cost": slip_cost, "source": src,
    }


def _record_sell_ledger_conn(
    conn,
    chat_id: int | str,
    t: str,
    n: int,
    p: float,
    trade_date: str,
    now: str,
    *,
    signal_price: float | None = None,
    source: str = "manual",
    record_exec: bool = True,
    clear_context: bool = False,
) -> dict:
    """Sinkronkan real_positions, real_trades, biaya BEI, & real_executions JUAL."""
    from core import jurnal

    pos = _reduce_real_position_conn(conn, chat_id, t, n, p, trade_date, now)
    avg_raw = float(pos.pop("avg_raw"))
    gross_pnl, net_pnl, net_pnl_pct = jurnal.calc_realized_pnl(n, p, avg_raw)
    fee_rp = round(gross_pnl - net_pnl)
    slip_rp, slip_pct, slip_cost = jurnal.calc_slippage("JUAL", p, signal_price, n)
    sig_val = float(signal_price) if signal_price and float(signal_price) > 0 else None
    src = str(source or "manual")
    if record_exec:
        conn.execute(
            "INSERT INTO real_executions"
            "(chat_id, ticker, side, lots, exec_price, signal_price, source,"
            " slippage_rp, slippage_pct, slippage_cost, avg_entry,"
            " gross_pnl, net_pnl, net_pnl_pct, trade_date, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(chat_id), t, "JUAL", n, p, sig_val, src,
             slip_rp, slip_pct, slip_cost, round(avg_raw, 4),
             gross_pnl, net_pnl, net_pnl_pct, str(trade_date), now))
    if clear_context:
        conn.execute(
            "DELETE FROM signal_context WHERE chat_id=? AND ticker=?",
            (str(chat_id), t))
    return {
        **pos,
        "exec_price": p,
        "signal_price": sig_val,
        "slippage_rp": slip_rp,
        "slippage_pct": slip_pct,
        "slippage_cost": slip_cost,
        "gross_pnl": gross_pnl,
        "fee_rp": fee_rp,
        "net_pnl": net_pnl,
        "net_realized": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "net_realized_pct": net_pnl_pct,
        "source": src,
    }


def add_real_position(chat_id: int | str, ticker: str, lots: int,
                      price: float, trade_date: str,
                      sector: str | None = None,
                      *,
                      record_exec: bool = True,
                      sl: float | None = None,
                      tp: float | None = None,
                      signal_price: float | None = None,
                      source: str = "portofolio") -> dict:
    """Catat pembelian asli + ledger eksekusi; harga rata-rata ditimbang lot."""
    n, p = _check_lot_price(lots, price)
    t = str(ticker).upper()
    now = _stamp()
    with _db() as conn:
        if record_exec:
            return _record_buy_ledger_conn(
                conn, chat_id, t, n, p, trade_date, now,
                signal_price=signal_price, sl=sl, tp=tp,
                source=source, sector=sector, clear_context=False,
            )
        return _add_real_position_conn(conn, chat_id, t, n, p,
                                       trade_date, sector, now)


def reduce_real_position(chat_id: int | str, ticker: str, lots: int,
                         price: float, trade_date: str,
                         *,
                         record_exec: bool = True,
                         signal_price: float | None = None,
                         source: str = "portofolio") -> dict:
    """Catat penjualan asli: realisasi bruto, fee BEI (0,40%), & realisasi neto."""
    n, p = _check_lot_price(lots, price)
    t = str(ticker).upper()
    now = _stamp()
    with _db() as conn:
        return _record_sell_ledger_conn(
            conn, chat_id, t, n, p, trade_date, now,
            signal_price=signal_price, source=source,
            record_exec=record_exec, clear_context=False,
        )


def real_positions(chat_id: int | str) -> list[dict]:
    """Posisi asli yang masih terbuka, urut kode."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT ticker, lots, avg_price, sector, updated_at"
            " FROM real_positions WHERE chat_id=? ORDER BY ticker",
            (str(chat_id),))
        return [dict(r) for r in rows]


def real_trades(chat_id: int | str, limit: int = 10) -> list[dict]:
    """Transaksi asli terakhir (BELI/JUAL)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT ticker, side, lots, price, trade_date"
            " FROM real_trades WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), max(1, int(limit))))
        return [dict(r) for r in rows]


# ------------------------------------------------- zona beli watchlist (P16)
def get_zone_states(chat_id: int | str) -> dict[str, str]:
    """Zona terakhir per emiten. Nilai "" berarti di luar zona."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT ticker, zone FROM zone_state WHERE chat_id=?",
            (str(chat_id),))
        return {str(r["ticker"]).upper(): str(r["zone"] or "") for r in rows}


def set_zone_state(chat_id: int | str, ticker: str, zone: str):
    """Simpan zona sekarang; "" menandai sudah keluar dari zona."""
    with _db() as conn:
        _ensure_chat(conn, chat_id)
        conn.execute(
            "INSERT INTO zone_state(chat_id, ticker, zone, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(chat_id, ticker) DO UPDATE SET"
            " zone=excluded.zone, updated_at=excluded.updated_at",
            (str(chat_id), str(ticker).upper(), str(zone or ""), _stamp()))


# ------------------------------------------- P28: jurnal eksekusi manual
def _normalize_intent(value: str | None) -> str | None:
    """Normalkan alias intent ('beli'/'buy' vs 'jual'/'sell'/'exit')."""
    raw = (value or "").strip().lower()
    if raw in ("beli", "buy"):
        return "beli"
    if raw in ("jual", "sell", "exit"):
        return "jual"
    return None


def _resolve_context_intent(source: str | None, intent: str | None = None,
                            mode: str | None = None) -> str:
    explicit = _normalize_intent(intent) or _normalize_intent(mode)
    if explicit:
        return explicit
    src = (source or "").strip().lower()
    if "jual" in src or "sell" in src or "exit" in src:
        return "jual"
    return "beli"


def _row_to_signal_dict(row) -> dict:
    d = dict(row)
    d.pop("sell_payload", None)
    d["intent"] = _normalize_intent(d.get("intent")) or "beli"
    d["mode"] = d["intent"]
    return d


def save_signal_context(
    chat_id: int | str,
    ticker: str,
    source: str,
    signal_price: float,
    sl: float | None = None,
    tp: float | None = None,
    suggested_lots: int | None = None,
    trade_date: str = "",
    *,
    intent: str | None = None,
    mode: str | None = None,
) -> None:
    """Simpan konteks sinyal per (chat, ticker) tanpa menimpa rencana beli oleh tanya jual."""
    if signal_price is None or float(signal_price) <= 0:
        return
    cid = str(chat_id)
    t = str(ticker).upper()
    now = _stamp()
    eff_intent = _resolve_context_intent(source, intent, mode)
    sl_v = float(sl) if sl else None
    tp_v = float(tp) if tp else None
    lots_v = int(suggested_lots) if suggested_lots and int(suggested_lots) > 0 else None
    src_v = str(source or ("tanya_jual" if eff_intent == "jual" else "manual"))
    tdate_v = str(trade_date or "")

    with _db() as conn:
        _ensure_chat(conn, cid)
        existing = conn.execute(
            "SELECT intent FROM signal_context WHERE chat_id=? AND ticker=?",
            (cid, t),
        ).fetchone()
        if eff_intent == "jual":
            sell_obj = {
                "ticker": t,
                "source": src_v,
                "signal_price": float(signal_price),
                "sl": sl_v,
                "tp": tp_v,
                "suggested_lots": lots_v,
                "trade_date": tdate_v,
                "updated_at": now,
                "intent": "jual",
                "mode": "jual",
            }
            sell_json = json.dumps(sell_obj)
            if existing and (_normalize_intent(existing["intent"]) or "beli") == "beli":
                # Tanya jual tidak boleh menimpa rencana/cek beli yang sudah disimpan.
                conn.execute(
                    "UPDATE signal_context SET sell_payload=? "
                    "WHERE chat_id=? AND ticker=?",
                    (sell_json, cid, t),
                )
                return
            conn.execute(
                "INSERT INTO signal_context"
                "(chat_id, ticker, source, signal_price, sl, tp, suggested_lots,"
                " trade_date, updated_at, intent, sell_payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(chat_id, ticker) DO UPDATE SET"
                " source=excluded.source, signal_price=excluded.signal_price,"
                " sl=excluded.sl, tp=excluded.tp,"
                " suggested_lots=excluded.suggested_lots,"
                " trade_date=excluded.trade_date, updated_at=excluded.updated_at,"
                " intent=excluded.intent, sell_payload=excluded.sell_payload",
                (cid, t, src_v, float(signal_price), sl_v, tp_v, lots_v,
                 tdate_v, now, "jual", sell_json),
            )
            return

        conn.execute(
            "INSERT INTO signal_context"
            "(chat_id, ticker, source, signal_price, sl, tp, suggested_lots,"
            " trade_date, updated_at, intent)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(chat_id, ticker) DO UPDATE SET"
            " source=excluded.source, signal_price=excluded.signal_price,"
            " sl=excluded.sl, tp=excluded.tp,"
            " suggested_lots=excluded.suggested_lots,"
            " trade_date=excluded.trade_date, updated_at=excluded.updated_at,"
            " intent='beli'",
            (cid, t, src_v, float(signal_price), sl_v, tp_v, lots_v,
             tdate_v, now, "beli"),
        )


def get_signal_context(
    chat_id: int | str,
    ticker: str,
    *,
    intent: str | None = None,
    mode: str | None = None,
) -> dict | None:
    """Ambil acuan sinyal terakhir (opsional filter intent 'beli' atau 'jual')."""
    want = _normalize_intent(intent) or _normalize_intent(mode)
    with _db() as conn:
        row = conn.execute(
            "SELECT ticker, source, signal_price, sl, tp, suggested_lots,"
            " trade_date, updated_at, intent, sell_payload FROM signal_context"
            " WHERE chat_id=? AND ticker=?",
            (str(chat_id), str(ticker).upper()),
        ).fetchone()
    if not row:
        return None
    row_intent = _normalize_intent(row["intent"]) or "beli"
    if want == "jual":
        raw_sell = row["sell_payload"]
        if raw_sell:
            try:
                parsed = json.loads(raw_sell)
                if isinstance(parsed, dict):
                    parsed["intent"] = "jual"
                    parsed["mode"] = "jual"
                    return parsed
            except (TypeError, ValueError):
                pass
        return _row_to_signal_dict(row) if row_intent == "jual" else None
    if want == "beli":
        return _row_to_signal_dict(row) if row_intent == "beli" else None
    return _row_to_signal_dict(row)


def clear_signal_context(
    chat_id: int | str,
    ticker: str,
    *,
    intent: str | None = None,
    mode: str | None = None,
) -> None:
    want = _normalize_intent(intent) or _normalize_intent(mode)
    cid = str(chat_id)
    t = str(ticker).upper()
    with _db() as conn:
        if want == "jual":
            row = conn.execute(
                "SELECT intent FROM signal_context WHERE chat_id=? AND ticker=?",
                (cid, t),
            ).fetchone()
            if not row:
                return
            if (_normalize_intent(row["intent"]) or "beli") == "beli":
                conn.execute(
                    "UPDATE signal_context SET sell_payload=NULL "
                    "WHERE chat_id=? AND ticker=?",
                    (cid, t),
                )
                return
        elif want == "beli":
            row = conn.execute(
                "SELECT intent FROM signal_context WHERE chat_id=? AND ticker=?",
                (cid, t),
            ).fetchone()
            if not row or (_normalize_intent(row["intent"]) or "beli") != "beli":
                return
        conn.execute(
            "DELETE FROM signal_context WHERE chat_id=? AND ticker=?",
            (cid, t),
        )


def record_execution_buy(
    chat_id: int | str,
    ticker: str,
    lots: int,
    exec_price: float,
    trade_date: str,
    *,
    signal_price: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "manual",
    sector: str | None = None,
) -> dict:
    """Catat eksekusi BELI manual Stockbit + sinkron ke portofolio asli."""
    n, p = _check_lot_price(lots, exec_price)
    t = str(ticker).upper()
    now = _stamp()
    with _db() as conn:
        return _record_buy_ledger_conn(
            conn, chat_id, t, n, p, trade_date, now,
            signal_price=signal_price, sl=sl, tp=tp,
            source=source, sector=sector, clear_context=True,
        )


def record_execution_sell(
    chat_id: int | str,
    ticker: str,
    lots: int,
    exec_price: float,
    trade_date: str,
    *,
    signal_price: float | None = None,
    source: str = "manual",
) -> dict:
    """Catat eksekusi JUAL manual + realisasi bruto/fee/neto setelah fee BEI."""
    n, p = _check_lot_price(lots, exec_price)
    t = str(ticker).upper()
    now = _stamp()
    with _db() as conn:
        return _record_sell_ledger_conn(
            conn, chat_id, t, n, p, trade_date, now,
            signal_price=signal_price, source=source,
            record_exec=True, clear_context=True,
        )


def real_executions(chat_id: int | str, limit: int = 50) -> list[dict]:
    """Daftar catatan eksekusi asli terbaru (termasuk realisasi & slippage)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, lots, exec_price, signal_price, sl, tp,"
            " source, slippage_rp, slippage_pct, slippage_cost, avg_entry,"
            " gross_pnl, net_pnl, net_pnl_pct, trade_date, created_at"
            " FROM real_executions WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), max(1, int(limit))))
        return [dict(r) for r in rows]


def open_execution_lots(chat_id: int | str, ticker: str) -> int:
    """Sisa lot yang pernah dicatat di real_executions (BELI minus JUAL)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side='BELI' THEN lots "
            "WHEN side='JUAL' THEN -lots ELSE 0 END), 0) AS net_lots"
            " FROM real_executions WHERE chat_id=? AND ticker=?",
            (str(chat_id), str(ticker).upper())).fetchone()
        return max(0, int(row["net_lots"])) if row else 0


def open_executions_by_ticker(chat_id: int | str) -> dict[str, dict]:
    """Eksekusi posisi terbuka per emiten: SL/TP tersimpan, entry rata-rata, & tanggal masuk."""
    cid = str(chat_id)
    with _db() as conn:
        pos_rows = conn.execute(
            "SELECT ticker, lots, avg_price FROM real_positions WHERE chat_id=?",
            (cid,),
        ).fetchall()
        open_pos = {str(r["ticker"]).upper(): int(r["lots"]) for r in pos_rows if int(r["lots"]) > 0}
        exec_rows = conn.execute(
            "SELECT id, ticker, side, lots, exec_price, signal_price, sl, tp,"
            " source, trade_date FROM real_executions"
            " WHERE chat_id=? ORDER BY id ASC",
            (cid,),
        ).fetchall()

    cycles: dict[str, dict] = {}
    for r in exec_rows:
        t = str(r["ticker"]).upper()
        side = str(r["side"] or "").upper()
        lots_r = int(r["lots"] or 0)
        px = float(r["exec_price"] or 0.0)
        st = cycles.setdefault(t, {
            "ticker": t, "lots": 0, "cost": 0.0,
            "sl": None, "tp": None, "trade_date": None,
            "latest_buy_date": None, "signal_price": None, "source": None,
        })
        if side == "BELI":
            if st["lots"] <= 0:
                st["trade_date"] = r["trade_date"]
            st["lots"] += lots_r
            st["cost"] += lots_r * 100.0 * px
            if r["sl"] is not None and float(r["sl"]) > 0:
                st["sl"] = float(r["sl"])
            if r["tp"] is not None and float(r["tp"]) > 0:
                st["tp"] = float(r["tp"])
            st["latest_buy_date"] = r["trade_date"]
            st["source"] = r["source"]
            if r["signal_price"] is not None and float(r["signal_price"]) > 0:
                st["signal_price"] = float(r["signal_price"])
        elif side == "JUAL":
            if st["lots"] > 0:
                avg_now = st["cost"] / (st["lots"] * 100.0)
                sold = min(st["lots"], lots_r)
                st["lots"] -= sold
                st["cost"] = st["lots"] * 100.0 * avg_now if st["lots"] > 0 else 0.0
            if st["lots"] <= 0:
                st.update({
                    "lots": 0, "cost": 0.0, "sl": None, "tp": None,
                    "trade_date": None, "latest_buy_date": None,
                    "signal_price": None, "source": None,
                })

    out: dict[str, dict] = {}
    for t, st in cycles.items():
        if st["lots"] <= 0:
            continue
        if open_pos and t not in open_pos:
            continue
        entry = st["cost"] / (st["lots"] * 100.0) if st["lots"] > 0 else 0.0
        out[t] = {
            "ticker": t,
            "lots": st["lots"],
            "entry": round(entry, 4),
            "sl": st["sl"],
            "tp": st["tp"],
            "trade_date": st["trade_date"],
            "latest_buy_date": st["latest_buy_date"],
            "signal_price": st["signal_price"],
            "source": st["source"],
        }
    return out


def execution_chats() -> list[str]:
    """Daftar chat_id yang punya catatan di jurnal eksekusi asli."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM real_executions").fetchall()
        return [str(r["chat_id"]) for r in rows]


def get_risk_limits(
    chat_id: int | str,
    default_pos: float = 0.25,
    default_sec: float = 0.40,
) -> tuple[float, float]:
    """Ambil batas posisi & sektor per chat (fraksi, mis. 0.25 dan 0.40)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM chat_settings "
            "WHERE chat_id=? AND key IN ('max_pos_pct', 'max_sector_pct')",
            (str(chat_id),),
        ).fetchall()
        cfg = {r["key"]: r["value"] for r in rows}
    try:
        pos = float(cfg["max_pos_pct"]) if "max_pos_pct" in cfg else default_pos
        sec = float(cfg["max_sector_pct"]) if "max_sector_pct" in cfg else default_sec
    except (TypeError, ValueError):
        return default_pos, default_sec
    if not (0.05 <= pos <= 0.50 and 0.10 <= sec <= 0.80 and sec >= pos):
        return default_pos, default_sec
    return pos, sec


def set_risk_limits(
    chat_id: int | str,
    max_pos_pct: float | None,
    max_sector_pct: float | None,
) -> tuple[float, float]:
    """Simpan atau reset batas posisi & sektor per chat (P23 lanjutan)."""
    from core import risk

    cid = str(chat_id)
    if max_pos_pct is None and max_sector_pct is None:
        with _db() as conn:
            conn.execute(
                "DELETE FROM chat_settings "
                "WHERE chat_id=? AND key IN ('max_pos_pct', 'max_sector_pct')",
                (cid,),
            )
        return risk.MAX_POS_PCT, risk.MAX_SECTOR_PCT

    pos, sec = risk.validate_limits(
        risk.MAX_POS_PCT if max_pos_pct is None else max_pos_pct,
        risk.MAX_SECTOR_PCT if max_sector_pct is None else max_sector_pct,
    )
    with _db() as conn:
        _ensure_chat(conn, cid)
        for key, val in (("max_pos_pct", str(pos)), ("max_sector_pct", str(sec))):
            conn.execute(
                "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
                (cid, key, val),
            )
    return pos, sec

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
CREATE TABLE IF NOT EXISTS daily_snapshots (
    trade_date   TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    close        REAL,
    volume       REAL,
    foreign_buy  REAL,
    foreign_sell REAL,
    foreign_net  REAL,
    pe           REAL,
    pbv          REAL,
    roe          REAL,
    der          REAL,
    div_yield    REAL,
    saved_at     TEXT NOT NULL,
    PRIMARY KEY (trade_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_daily_snapshots_ticker_date
    ON daily_snapshots(ticker, trade_date);
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



# Re-export fungsi domain agar seluruh pemanggil (`from core import store`)
# dan monkeypatch di test suite tetap 100% kompatibel.
from core.store_portfolio import (  # noqa: E402, F401
    _add_real_position_conn,
    _check_lot_price,
    _normalize_intent,
    _record_buy_ledger_conn,
    _record_sell_ledger_conn,
    _reduce_real_position_conn,
    _resolve_context_intent,
    _row_to_signal_dict,
    _stamp,
    add_real_position,
    all_papers,
    clear_signal_context,
    delete_paper,
    execution_chats,
    get_paper,
    get_risk_limits,
    get_signal_context,
    open_execution_lots,
    open_executions_by_ticker,
    prune_paper,
    real_executions,
    real_positions,
    real_trades,
    record_execution_buy,
    record_execution_sell,
    reduce_real_position,
    save_signal_context,
    set_paper_record,
    set_risk_limits,
)
from core.store_profile import (  # noqa: E402, F401
    _ONBOARD_STATE_KEY,
    _THESIS_KEY_PREFIX,
    _USER_MODE_ALIASES,
    _USER_MODE_KEY,
    DEFAULT_USER_MODE,
    USER_MODES,
    _decode_onboard_state,
    _decode_thesis_state,
    _read_chat_setting,
    _stored_user_mode,
    _thesis_key,
    _upsert_chat_setting,
    clear_onboard_state,
    clear_position_thesis,
    get_all_position_theses,
    get_onboard_state,
    get_position_thesis,
    get_user_mode,
    has_user_mode,
    normalize_user_mode,
    set_onboard_state,
    set_position_thesis,
    set_user_mode,
    get_syariah_mode,
    set_syariah_mode,
)
from core.store_scan import (  # noqa: E402, F401
    _HISTORY_COLS,
    _SCAN_COLS,
    HISTORY_KEEP_DAYS,
    _rotate_backups,
    append_scan_history,
    backup_to,
    close_scan_history,
    closed_scan_history,
    load_market_scan,
    load_plan_cache,
    market_scan_counts,
    market_scan_meta,
    pending_scan_history,
    load_daily_snapshots,
    save_daily_snapshots,
    save_market_scan,
    save_plan_cache,
    scan_history_counts,
)
from core.store_watchlist import (  # noqa: E402, F401
    alert_subscriptions,
    counts,
    get_alerts,
    get_auto,
    get_auto_chats,
    get_modal,
    get_watchlist,
    get_zone_states,
    has_div_reminder,
    mark_div_reminder,
    remove_alert,
    set_alert,
    set_auto,
    set_modal,
    set_zone_state,
    update_alert_state,
    update_score_band,
    watchlist_add,
    watchlist_remove,
)

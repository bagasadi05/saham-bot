"""Domain penyimpanan: paper trading, portofolio asli, jurnal eksekusi manual, dan batas risiko."""
from __future__ import annotations

import datetime as dt

import config
import json
import math

from core import store as _store

# ---------------------------------------------------------------- paper -----

def get_paper(chat_id: int | str) -> dict[str, dict]:
    with _store._db() as conn:
        rows = conn.execute("SELECT symbol, rec FROM paper WHERE chat_id=?",
                            (str(chat_id),))
        return {r["symbol"]: json.loads(r["rec"]) for r in rows}


def set_paper_record(chat_id: int | str, symbol: str, rec: dict):
    with _store._db() as conn:
        _store._ensure_chat(conn, chat_id)
        conn.execute(
            "INSERT INTO paper(chat_id, symbol, rec) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, symbol) DO UPDATE SET rec=excluded.rec",
            (str(chat_id), symbol,
             json.dumps(rec, ensure_ascii=False, default=str)))


def delete_paper(chat_id: int | str, symbol: str):
    with _store._db() as conn:
        conn.execute("DELETE FROM paper WHERE chat_id=? AND symbol=?",
                     (str(chat_id), symbol))


def all_papers() -> list[tuple[str, str, dict]]:
    """Semua posisi latihan: [(chat_id, symbol, rec), ...]."""
    with _store._db() as conn:
        rows = conn.execute("SELECT chat_id, symbol, rec FROM paper")
        return [(r["chat_id"], r["symbol"], json.loads(r["rec"])) for r in rows]


def prune_paper(chat_id: int | str, keep_closed: int = _store.KEEP_CLOSED) -> int:
    """Pangkas riwayat posisi latihan yang sudah selesai ('closed').

    Tabel paper dulu tak pernah dibersihkan: setelah berbulan-bulan, `/saran
    list` dan rekap PnL ikut membengkak tanpa batas. Sisakan `keep_closed`
    posisi selesai TERBARU (urut filled_at/queued); posisi open & pending
    tidak pernah disentuh. Return jumlah baris yang dihapus.
    """
    if keep_closed < 0:
        return 0
    with _store._db() as conn:
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
    if not math.isfinite(p) or p <= 0:
        raise ValueError("harga harus berupa angka positif yang valid")
    return n, p


def _add_real_position_conn(conn, chat_id: int | str, t: str, n: int, p: float,
                            trade_date: str, sector: str | None,
                            now: str) -> dict:
    _store._ensure_chat(conn, chat_id)
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
        cur = conn.execute(
            "UPDATE real_positions SET lots = lots - ?, updated_at=?"
            " WHERE chat_id=? AND ticker=? AND lots >= ?",
            (n, now, str(chat_id), t, n))
    else:
        cur = conn.execute(
            "DELETE FROM real_positions WHERE chat_id=? AND ticker=? AND lots = ?",
            (str(chat_id), t, n))
    if cur.rowcount != 1:
        raise ValueError(f"posisi {t} berubah bersamaan; coba lagi")
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
    if int(pos.get("lots_left") or 0) <= 0:
        conn.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?",
            (str(chat_id), _store._thesis_key(t)))
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
    with _store._db() as conn:
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
    with _store._db() as conn:
        return _record_sell_ledger_conn(
            conn, chat_id, t, n, p, trade_date, now,
            signal_price=signal_price, source=source,
            record_exec=record_exec, clear_context=False,
        )


def real_positions(chat_id: int | str) -> list[dict]:
    """Posisi asli yang masih terbuka, urut kode."""
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT ticker, lots, avg_price, sector, updated_at"
            " FROM real_positions WHERE chat_id=? ORDER BY ticker",
            (str(chat_id),))
        return [dict(r) for r in rows]


def real_trades(chat_id: int | str, limit: int = 10) -> list[dict]:
    """Transaksi asli terakhir (BELI/JUAL)."""
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT ticker, side, lots, price, trade_date"
            " FROM real_trades WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), max(1, int(limit))))
        return [dict(r) for r in rows]



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

    with _store._db() as conn:
        _store._ensure_chat(conn, cid)
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
    with _store._db() as conn:
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
    with _store._db() as conn:
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
    with _store._db() as conn:
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
    with _store._db() as conn:
        return _record_sell_ledger_conn(
            conn, chat_id, t, n, p, trade_date, now,
            signal_price=signal_price, source=source,
            record_exec=True, clear_context=True,
        )


def real_executions(chat_id: int | str, limit: int = 50) -> list[dict]:
    """Daftar catatan eksekusi asli terbaru (termasuk realisasi & slippage)."""
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, lots, exec_price, signal_price, sl, tp,"
            " source, slippage_rp, slippage_pct, slippage_cost, avg_entry,"
            " gross_pnl, net_pnl, net_pnl_pct, trade_date, created_at"
            " FROM real_executions WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), max(1, int(limit))))
        return [dict(r) for r in rows]


def _rebuild_position_from_executions_conn(
    conn,
    chat_id: int | str,
    ticker: str,
    now: str,
) -> tuple[int, float]:
    """Hitung ulang real_positions & PnL realisasi JUAL dari sisa real_executions.

    Mengembalikan (lots_tersisa, avg_price). Melempar ValueError jika urutan
    sisa eksekusi tidak valid (mis. ada JUAL yang melebihi lot yang dimiliki).
    """
    from core import jurnal

    cid = str(chat_id)
    t = str(ticker).upper()
    old_pos = conn.execute(
        "SELECT sector FROM real_positions WHERE chat_id=? AND ticker=?",
        (cid, t),
    ).fetchone()
    old_sector = old_pos["sector"] if old_pos else None

    execs = conn.execute(
        "SELECT id, side, lots, exec_price FROM real_executions"
        " WHERE chat_id=? AND ticker=? ORDER BY id ASC",
        (cid, t),
    ).fetchall()

    held = 0
    avg = 0.0
    for r in execs:
        side = str(r["side"] or "").upper()
        n = int(r["lots"] or 0)
        p = float(r["exec_price"] or 0.0)
        if side == "BELI":
            new_held = held + n
            avg = ((held * avg) + (n * p)) / new_held if new_held > 0 else 0.0
            held = new_held
        elif side == "JUAL":
            if n > held:
                raise ValueError(
                    f"Tidak bisa menghapus BELI {t} ini karena ada transaksi JUAL "
                    f"sesudahnya ({n} lot > sisa {held} lot). Hapus transaksi JUAL terkait lebih dulu."
                )
            gross_pnl, net_pnl, net_pnl_pct = jurnal.calc_realized_pnl(n, p, avg)
            conn.execute(
                "UPDATE real_executions SET avg_entry=?, gross_pnl=?, net_pnl=?, net_pnl_pct=?"
                " WHERE id=?",
                (round(avg, 4), gross_pnl, net_pnl, net_pnl_pct, int(r["id"])),
            )
            held -= n
            if held <= 0:
                held = 0
                avg = 0.0

    if held > 0:
        conn.execute(
            "INSERT INTO real_positions(chat_id, ticker, lots, avg_price, sector, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(chat_id, ticker) DO UPDATE SET"
            " lots=excluded.lots, avg_price=excluded.avg_price,"
            " sector=COALESCE(real_positions.sector, excluded.sector),"
            " updated_at=excluded.updated_at",
            (cid, t, held, avg, old_sector, now),
        )
    else:
        conn.execute(
            "DELETE FROM real_positions WHERE chat_id=? AND ticker=?",
            (cid, t),
        )
        conn.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?",
            (cid, _store._thesis_key(t)),
        )
    return held, round(avg, 4)


def delete_execution(chat_id: int | str, exec_id: int) -> dict:
    """Hapus satu catatan eksekusi BELI/JUAL salah input & sinkronkan portofolio.

    Seluruh perubahan dijalankan atomik: jika penghapusan BELI menyebabkan JUAL
    sesudahnya kekurangan lot, transaksi dibatalkan (rollback) dengan pesan jelas.
    """
    cid = str(chat_id)
    try:
        eid = int(exec_id)
    except (TypeError, ValueError):
        raise ValueError("ID eksekusi tidak valid") from None
    now = _stamp()
    with _store._db() as conn:
        row = conn.execute(
            "SELECT id, ticker, side, lots, exec_price, trade_date, created_at"
            " FROM real_executions WHERE chat_id=? AND id=?",
            (cid, eid),
        ).fetchone()
        if not row:
            raise KeyError(f"Catatan eksekusi #{eid} tidak ditemukan")
        t = str(row["ticker"]).upper()
        side = str(row["side"]).upper()
        lots = int(row["lots"])
        px = float(row["exec_price"])
        created_at = str(row["created_at"] or "")

        conn.execute("DELETE FROM real_executions WHERE chat_id=? AND id=?", (cid, eid))

        # Hapus juga 1 baris pasangan di real_trades agar riwayat tetap sinkron.
        tr_row = conn.execute(
            "SELECT id FROM real_trades"
            " WHERE chat_id=? AND ticker=? AND side=? AND lots=? AND ABS(price - ?) < 1e-6"
            " ORDER BY CASE WHEN created_at=? THEN 0 ELSE 1 END, id DESC LIMIT 1",
            (cid, t, side, lots, px, created_at),
        ).fetchone()
        if tr_row:
            conn.execute("DELETE FROM real_trades WHERE id=?", (int(tr_row["id"]),))

        lots_left, avg_price = _rebuild_position_from_executions_conn(conn, cid, t, now)
        return {
            "deleted_id": eid,
            "ticker": t,
            "side": side,
            "lots": lots,
            "exec_price": px,
            "lots_left": lots_left,
            "avg_price": avg_price,
        }


def delete_real_position(chat_id: int | str, ticker: str) -> dict:
    """Hapus seluruh posisi terbuka & riwayat eksekusi satu emiten (salah input)."""
    cid = str(chat_id)
    t = str(ticker or "").strip().upper()
    if not t:
        raise ValueError("Kode saham tidak boleh kosong")
    with _store._db() as conn:
        cur_pos = conn.execute(
            "DELETE FROM real_positions WHERE chat_id=? AND ticker=?",
            (cid, t),
        )
        cur_exec = conn.execute(
            "DELETE FROM real_executions WHERE chat_id=? AND ticker=?",
            (cid, t),
        )
        cur_tr = conn.execute(
            "DELETE FROM real_trades WHERE chat_id=? AND ticker=?",
            (cid, t),
        )
        conn.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?",
            (cid, _store._thesis_key(t)),
        )
        conn.execute(
            "DELETE FROM signal_context WHERE chat_id=? AND ticker=?",
            (cid, t),
        )
        if cur_pos.rowcount == 0 and cur_exec.rowcount == 0 and cur_tr.rowcount == 0:
            raise KeyError(f"Tidak ada posisi atau catatan eksekusi untuk {t}")
        return {
            "ticker": t,
            "deleted_positions": int(cur_pos.rowcount),
            "deleted_executions": int(cur_exec.rowcount),
            "deleted_trades": int(cur_tr.rowcount),
        }


def open_execution_lots(chat_id: int | str, ticker: str) -> int:
    """Sisa lot yang pernah dicatat di real_executions (BELI minus JUAL)."""
    with _store._db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side='BELI' THEN lots "
            "WHEN side='JUAL' THEN -lots ELSE 0 END), 0) AS net_lots"
            " FROM real_executions WHERE chat_id=? AND ticker=?",
            (str(chat_id), str(ticker).upper())).fetchone()
        return max(0, int(row["net_lots"])) if row else 0


def open_executions_by_ticker(chat_id: int | str) -> dict[str, dict]:
    """Eksekusi posisi terbuka per emiten: SL/TP tersimpan, entry rata-rata, & tanggal masuk."""
    cid = str(chat_id)
    with _store._db() as conn:
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
        if t not in open_pos:
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
    with _store._db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM real_executions").fetchall()
        return [str(r["chat_id"]) for r in rows]


def get_risk_limits(
    chat_id: int | str,
    default_pos: float = config.DEFAULT_MAX_POS_PCT,
    default_sec: float = 0.40,
) -> tuple[float, float]:
    """Ambil batas posisi & sektor per chat (fraksi, mis. 0.25 dan 0.40)."""
    with _store._db() as conn:
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
        with _store._db() as conn:
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
    with _store._db() as conn:
        _store._ensure_chat(conn, cid)
        for key, val in (("max_pos_pct", str(pos)), ("max_sector_pct", str(sec))):
            conn.execute(
                "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
                (cid, key, val),
            )
    return pos, sec



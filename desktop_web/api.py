"""FastAPI local backend untuk SahamBot Modern Desktop (WebView2).

Seluruh logika domain tetap berasal dari `core/desktop_monitor.py` dan `core/store*.py`
sehingga tidak ada duplikasi aturan analisis antara Telegram, Tkinter, dan Modern UI.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import datetime as dt
import hashlib
import hmac
import io
import json
import math
import os
import secrets
import time
from pathlib import Path
import socket
import threading
from typing import Any

import config  # noqa: F401 — muat .env saat dijalankan langsung via uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd
from pydantic import BaseModel

from core import (
    accuracy_tracker,
    alerts as alertsmod,
    analysis as analysismod,
    banding,
    data as datamod,
    desktop_helpers as dh,
    desktop_monitor as dm,
    pro_analyzer,
    store_portfolio,
    store_profile,
    store_watchlist,
    syariah as syariahmod,
    universe,
)
from desktop_web import analysis_enhancer, stockbit_bridge

STATIC_DIR = Path(__file__).resolve().parent / "static"
_PIN_FILE = Path(__file__).resolve().parent.parent / "temp" / "mobile_pin.json"
_AUTH_COOKIE = "sahambot_pin_auth"

app = FastAPI(title="SahamBot Desktop API", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_PIN_KDF_ITER = 120_000
_PIN_FILE_SCHEMA = 2
_SESSION_TTL_S = 30 * 24 * 3600
_ENV_SALT = hashlib.sha256(b"sahambot-pin-salt").hexdigest()


def _hash_pin(pin: str, salt_hex: str) -> str:
    """Hash PIN dengan PBKDF2-HMAC-SHA256 (KDF), bukan SHA-256 polos."""
    return hashlib.pbkdf2_hmac(
        "sha256", f"sahambot:{pin}".encode(), bytes.fromhex(salt_hex), _PIN_KDF_ITER,
    ).hex()


def _load_pin_record() -> dict:
    try:
        if _PIN_FILE.is_file():
            data = json.loads(_PIN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _env_pin_hash() -> str:
    """Hash PIN dari env (KDF dengan salt aplikasi agar deterministik)."""
    env_pin = os.environ.get("SAHAMBOT_WEB_PIN", "").strip()
    if not env_pin:
        return ""
    return _hash_pin(env_pin, _ENV_SALT)


def _load_pin_hash() -> str:
    """Hash PIN aktif: dari env (KDF) atau file lokal (KDF baru / legacy sha256)."""
    env_h = _env_pin_hash()
    if env_h:
        return env_h
    rec = _load_pin_record()
    return str(rec.get("pin_kdf") or rec.get("pin_hash") or "").strip()


def _pin_file_salt() -> str:
    salt = str(_load_pin_record().get("salt") or "")
    return salt if len(salt) == 64 else ""


def _verify_pin(pin: str) -> bool:
    """Verifikasi PIN terhadap env atau file (mendukung legacy sha256)."""
    if _env_pin_hash():
        return hmac.compare_digest(_env_pin_hash(), _hash_pin(pin, _ENV_SALT))
    rec = _load_pin_record()
    kdf = str(rec.get("pin_kdf") or "")
    if kdf and _pin_file_salt():
        return hmac.compare_digest(kdf, _hash_pin(pin, _pin_file_salt()))
    legacy = str(rec.get("pin_hash") or "")
    if legacy:
        return hmac.compare_digest(legacy, hashlib.sha256(f"sahambot:{pin}".encode()).hexdigest())
    return False


def _save_pin_hash(pin: str) -> bool:
    """Simpan PIN sebagai salt + PBKDF2; PIN kosong = hapus file (PIN mati)."""
    clean = pin.strip()
    if not clean:
        try:
            if _PIN_FILE.is_file():
                _PIN_FILE.unlink()
        except OSError:
            pass
        return False
    salt = secrets.token_hex(32)
    rec = {
        "schema": _PIN_FILE_SCHEMA,
        "salt": salt,
        "pin_kdf": _hash_pin(clean, salt),
        "sessions": {},
    }
    _PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PIN_FILE.write_text(json.dumps(rec), encoding="utf-8")
    return True


def _create_session() -> str:
    """Buat session token acak; server hanya menyimpan hash-nya di file PIN."""
    token = secrets.token_urlsafe(32)
    rec = _load_pin_record()
    sessions = rec.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    now = time.time()
    sessions = {k: v for k, v in sessions.items() if isinstance(v, (int, float)) and v > now}
    sessions[hashlib.sha256(token.encode()).hexdigest()] = now + _SESSION_TTL_S
    rec["sessions"] = sessions
    try:
        _PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PIN_FILE.write_text(json.dumps(rec), encoding="utf-8")
    except OSError:
        pass
    return token


def _valid_session(cookie: str) -> bool:
    """Cookie valid bila hash-nya terdaftar & belum kedaluwarsa (constant-time)."""
    if not cookie:
        return False
    sessions = _load_pin_record().get("sessions")
    if not isinstance(sessions, dict):
        return False
    now = time.time()
    token_hash = hashlib.sha256(cookie.encode()).hexdigest()
    for k, v in sessions.items():
        if isinstance(v, (int, float)) and v > now and hmac.compare_digest(k, token_hash):
            return True
    return False


def _is_local_request(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


_UNLOCK_MAX_ATTEMPTS = 5
_UNLOCK_WINDOW_S = 300
_UNLOCK_LOCKOUT_S = 300
_unlock_lock = threading.Lock()
_unlock_failures: dict[str, list[float]] = {}
_unlock_locked_until: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    """Identitas IP sumber langsung dari koneksi socket (tidak percaya X-Forwarded-For)."""
    return ((request.client.host if request.client else "") or "unknown").strip()


def _reset_unlock_rate_limit() -> None:
    """Bersihkan seluruh catatan gagal & lockout unlock PIN."""
    with _unlock_lock:
        _unlock_failures.clear()
        _unlock_locked_until.clear()


def _unlock_lockout_remaining(ip: str, now: float | None = None) -> int:
    """Kembalikan sisa detik lockout untuk IP ini (0 bila tidak terkunci)."""
    ts = time.time() if now is None else float(now)
    with _unlock_lock:
        until = _unlock_locked_until.get(ip, 0.0)
        if until > ts:
            return max(1, math.ceil(until - ts))
        _unlock_locked_until.pop(ip, None)
        return 0


def _record_unlock_failure(ip: str, now: float | None = None) -> tuple[int, int]:
    """Catat kegagalan PIN; kembalikan ``(sisa_percobaan, detik_lockout)``."""
    ts = time.time() if now is None else float(now)
    cutoff = ts - _UNLOCK_WINDOW_S
    with _unlock_lock:
        recent = [t for t in _unlock_failures.get(ip, []) if t > cutoff]
        recent.append(ts)
        if len(recent) >= _UNLOCK_MAX_ATTEMPTS:
            _unlock_failures.pop(ip, None)
            _unlock_locked_until[ip] = ts + _UNLOCK_LOCKOUT_S
            return 0, _UNLOCK_LOCKOUT_S
        _unlock_failures[ip] = recent
        return _UNLOCK_MAX_ATTEMPTS - len(recent), 0


def _clear_unlock_failures(ip: str) -> None:
    """Hapus catatan gagal & lockout IP setelah PIN valid."""
    with _unlock_lock:
        _unlock_failures.pop(ip, None)
        _unlock_locked_until.pop(ip, None)


def _detect_lan_urls(port: int) -> list[dict[str, str]]:
    """Deteksi alamat IPv4 LAN (Wi-Fi) dan Tailscale (100.x.x.x) untuk akses HP."""
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary = s.getsockname()[0]
            if primary and not primary.startswith("127."):
                ips.append(primary)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    urls: list[dict[str, str]] = []
    for ip in ips:
        if ip.startswith("100."):
            label = "Tailscale (Dari Mana Saja)"
        elif ip.startswith(("192.168.", "10.", "172.")):
            label = "Wi-Fi Lokal (Satu Jaringan)"
        else:
            label = "Jaringan Lokal"
        urls.append({"label": label, "ip": ip, "url": f"http://{ip}:{port}/"})
    return urls


def _lan_enabled() -> bool:
    return os.environ.get("SAHAMBOT_LAN", "").strip().lower() in ("1", "true", "yes", "on")


@app.middleware("http")
async def _mobile_pin_guard(request: Request, call_next: Any) -> Any:
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/health", "/api/auth/unlock", "/api/mobile-info"):
        if not _is_local_request(request):
            pin_set = bool(_load_pin_hash())
            if _lan_enabled() and not pin_set:
                # Mode LAN tanpa PIN: tolak akses, jangan buka API ke jaringan.
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Mode LAN aktif tanpa PIN. Set PIN dari PC (ikon gembok) untuk membuka akses HP."},
                )
            if pin_set:
                cookie = request.cookies.get(_AUTH_COOKIE, "")
                if not _valid_session(cookie):
                    return JSONResponse(status_code=401, content={"detail": "PIN akses HP diperlukan."})
    resp = await call_next(request)
    if path == "/" or path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp

_scan_lock = dm._SCAN_LOCK
_scan_state = dm._SCAN_STATE
_last_regime_cache: dict[str, Any] | None = None


def _json_safe(obj: Any) -> Any:
    """Konversi objek Python/NumPy/Pandas ke struktur JSON valid (NaN/Inf -> None)."""
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, pd.DataFrame):
        return None
    if isinstance(obj, Counter):
        return {str(k): int(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items() if k != "ind" and k != "df"}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    # NumPy scalar fallback
    item_fn = getattr(obj, "item", None)
    if callable(item_fn):
        return _json_safe(item_fn())
    return str(obj)


def _extract_candles(ind: pd.DataFrame | None, limit: int = 240) -> list[dict[str, Any]]:
    """Konversi DataFrame indikator menjadi array candle siap TradingView Lightweight Charts."""
    if not isinstance(ind, pd.DataFrame) or ind.empty:
        return []
    sub = ind.tail(limit)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in sub.iterrows():
        t_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        if t_str in seen:
            continue
        o = dh._safe_float(row.get("open"))
        h = dh._safe_float(row.get("high"))
        lo = dh._safe_float(row.get("low"))
        c = dh._safe_float(row.get("close"))
        if None in (o, h, lo, c):
            continue
        seen.add(t_str)
        out.append({
            "time": t_str,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(lo, 2),
            "close": round(c, 2),
            "volume": int(dh._safe_float(row.get("volume"), 0.0) or 0.0),
            "sma20": round(dh._safe_float(row.get("sma20")), 2) if dh._safe_float(row.get("sma20")) else None,
            "sma50": round(dh._safe_float(row.get("sma50")), 2) if dh._safe_float(row.get("sma50")) else None,
            "sma200": round(dh._safe_float(row.get("sma200")), 2) if dh._safe_float(row.get("sma200")) else None,
        })
    return out


def _calc_poc_60d(candles: list[dict[str, Any]]) -> float | None:
    """Hitung Point of Control (level harga volume tertinggi 60 bar terakhir)."""
    sub = candles[-60:]
    if len(sub) < 10:
        return None
    lows = [c["low"] for c in sub if c.get("low") is not None]
    highs = [c["high"] for c in sub if c.get("high") is not None]
    if not lows or not highs:
        return None
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return round(lo, 2)
    bins = 24
    step = (hi - lo) / bins
    buckets = [0.0] * bins
    for c in sub:
        vol = float(c.get("volume") or 0.0)
        if vol <= 0:
            continue
        mid = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        idx = min(bins - 1, max(0, int((mid - lo) / step)))
        buckets[idx] += vol
    if max(buckets) <= 0:
        return None
    best_i = max(range(bins), key=lambda i: buckets[i])
    return round(lo + (best_i + 0.5) * step, 2)


class ProfilePayload(BaseModel):
    chat_id: str = dm.DEFAULT_PROFILE_ID
    modal: float = float(dh.DEFAULT_MODAL_RP)
    syariah: bool = False
    refresh: str | None = None


class WatchPayload(BaseModel):
    chat_id: str = dm.DEFAULT_PROFILE_ID
    ticker: str


class ExecutionPayload(BaseModel):
    chat_id: str = dm.DEFAULT_PROFILE_ID
    ticker: str
    side: str
    price: float
    lots: int
    notes: str = ""


class AlertPayload(BaseModel):
    chat_id: str = dm.DEFAULT_PROFILE_ID
    ticker: str
    kinds: list[str]


class AccuracyRecordPayload(BaseModel):
    ticker: str
    verdict: str = "TUNGGU"
    entry_low: float
    entry_high: float
    sl: float
    tp1: float
    tp2: float | None = None
    regime: str | None = None
    power_score: float | None = None
    notes: str = ""


class PinPayload(BaseModel):
    pin: str = ""


class BulkImportPayload(BaseModel):
    chat_id: str = dm.DEFAULT_PROFILE_ID
    raw_text: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/manifest.json")
async def pwa_manifest() -> dict[str, Any]:
    return {
        "name": "SahamBot — Terminal Analisis BEI",
        "short_name": "SahamBot",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#050811",
        "theme_color": "#050811",
        "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png"}],
    }


@app.get("/icon.png")
async def pwa_icon() -> Response:
    img = dh.create_tray_icon_image(192)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/mobile-info")
async def get_mobile_info(request: Request) -> dict[str, Any]:
    port = int(os.environ.get("SAHAMBOT_ACTIVE_PORT", "18765") or 18765)
    lan_on = os.environ.get("SAHAMBOT_LAN", "").strip().lower() in ("1", "true", "yes", "on")
    pin_hash = _load_pin_hash()
    is_local = _is_local_request(request)
    cookie = request.cookies.get(_AUTH_COOKIE, "")
    unlocked = is_local or (not pin_hash) or _valid_session(cookie)
    return {
        "ok": True,
        "lan_enabled": lan_on,
        "port": port,
        "urls": _detect_lan_urls(port),
        "pin_enabled": bool(pin_hash),
        "is_local": is_local,
        "auth_required": not unlocked,
    }


@app.post("/api/mobile-pin")
async def set_mobile_pin(body: PinPayload, request: Request) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Pengaturan PIN hanya bisa diubah langsung dari PC.")
    pin = body.pin.strip()
    if pin and (len(pin) < 4 or len(pin) > 16):
        raise HTTPException(status_code=400, detail="PIN minimal 4 karakter (atau kosongkan untuk mematikan PIN).")
    enabled = _save_pin_hash(pin)
    _reset_unlock_rate_limit()
    return {"ok": True, "pin_enabled": enabled}


@app.post("/api/auth/unlock")
async def unlock_mobile(body: PinPayload, request: Request) -> JSONResponse:
    ip = _client_ip(request)
    wait_s = _unlock_lockout_remaining(ip)
    if wait_s > 0:
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"Terlalu banyak percobaan PIN salah. Coba lagi dalam {wait_s} detik.",
                "retry_after": wait_s,
            },
            headers={"Retry-After": str(wait_s)},
        )
    if not _load_pin_hash():
        return JSONResponse({"ok": True})
    if not _verify_pin(body.pin.strip()):
        remaining, locked_s = _record_unlock_failure(ip)
        if locked_s > 0:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Terlalu banyak percobaan PIN salah. Akses dikunci sementara selama {locked_s} detik.",
                    "retry_after": locked_s,
                },
                headers={"Retry-After": str(locked_s)},
            )
        raise HTTPException(status_code=401, detail=f"PIN salah. Sisa percobaan: {remaining}.")
    _clear_unlock_failures(ip)
    token = _create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        _AUTH_COOKIE,
        token,
        max_age=_SESSION_TTL_S,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.get("/api/health")
async def health() -> dict[str, Any]:
    vendor_ok = all(
        (STATIC_DIR / "vendor" / f).is_file()
        for f in ("tailwindcss.js", "lucide.min.js", "lightweight-charts.standalone.production.js", "qrcode.min.js")
    )
    return {
        "ok": True,
        "app": "sahambot-desktop",
        "stockbit_mcp": stockbit_bridge.is_enabled(),
        "eodhd_fallback": bool(os.environ.get("EODHD_API_KEY", "").strip()),
        "vendor_offline_ready": vendor_ok,
        "pro_analyzer": True,
    }


@app.get("/api/universe")
async def get_universe(
    q: str = Query(""),
    syariah: bool = Query(False),
    limit: int = Query(1000),
) -> dict[str, Any]:
    """Daftar emiten BEI (kode, nama, sektor, papan, status ISSI) untuk dropdown pilih saham."""
    ql = q.strip().lower()
    items: list[dict[str, Any]] = []
    for row in universe.stocks():
        code = str(row.get("code") or "").strip().upper()
        if not code:
            continue
        is_sy = bool(syariahmod.is_syariah(code))
        if syariah and not is_sy:
            continue
        name = str(row.get("name") or "")
        sector = universe.sector_of(code)
        if ql and ql not in code.lower() and ql not in name.lower() and ql not in sector.lower():
            continue
        items.append({
            "code": code,
            "name": name,
            "sector": sector,
            "board": str(row.get("board") or ""),
            "syariah": is_sy,
        })
        if len(items) >= max(1, min(1200, limit)):
            break
    return {"ok": True, "total": len(items), "items": items}


@app.get("/api/stockbit/{ticker}")
async def get_stockbit_live(ticker: str) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    data = await asyncio.to_thread(stockbit_bridge.fetch_stockbit_snapshot, sym)
    return _json_safe(data)


@app.get("/api/backtest/{ticker}")
async def get_backtest_5y(ticker: str) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    res = await asyncio.to_thread(analysis_enhancer.run_backtest_summary, sym)
    return _json_safe(res)


@app.get("/api/backtest/{ticker}/simulate")
async def simulate_backtest(
    ticker: str,
    period: str = Query("3y"),
    modal: float = Query(float(dh.DEFAULT_MODAL_RP)),
    sl_pct: float = Query(5.0),
    tp_pct: float = Query(10.0),
    max_hold: int = Query(20),
    strategy: str = Query("signal"),
) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    res = await asyncio.to_thread(
        analysis_enhancer.simulate_equity_backtest,
        sym,
        period=period,
        modal=modal,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        max_hold=max_hold,
        strategy=strategy,
    )
    return _json_safe(res)


@app.get("/api/compare")
async def compare_tickers(
    tickers: str = Query("BBCA,BBRI,BRIS"),
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
    modal: float = Query(float(dh.DEFAULT_MODAL_RP)),
) -> dict[str, Any]:
    raw_list = [t.strip().upper().replace(".JK", "") for t in tickers.replace(" ", ",").split(",") if t.strip()]
    uniq: list[str] = []
    for sym in raw_list:
        if sym and sym not in uniq:
            uniq.append(sym)
    uniq = uniq[:4]
    if not uniq:
        raise HTTPException(status_code=400, detail="Masukkan 1–4 kode saham (mis. BBCA,BBRI,BRIS).")
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID

    async def _one(sym: str) -> dict[str, Any]:
        try:
            detail = await asyncio.to_thread(
                dm.inspect_ticker_detail,
                sym,
                modal,
                chat_id=cid,
                period="1y",
                market_regime=_last_regime_cache,
                include_bandar=False,
            )
        except Exception as exc:
            return {"ticker": sym, "ok": False, "error": str(exc)}
        snap = (detail.get("res") or {}).get("snap") or {}
        cek = detail.get("cek") or {}
        ps = detail.get("powerscore") or {}
        fund = detail.get("fund") or {}
        plan = detail.get("plan") or {}
        sb = (
            await asyncio.to_thread(stockbit_bridge.fetch_stockbit_snapshot, sym)
            if stockbit_bridge.is_enabled()
            else {"available": False}
        )
        return {
            "ok": True,
            "ticker": sym,
            "name": detail.get("name") or sym,
            "sector": detail.get("sector") or "-",
            "syariah": bool(detail.get("syariah")),
            "close": snap.get("close"),
            "ret1d": snap.get("ret1d"),
            "ret20d": snap.get("ret20d"),
            "rsi": snap.get("rsi"),
            "verdict": cek.get("verdict") or "TUNGGU",
            "points": cek.get("points"),
            "powerscore_pct": ps.get("pct"),
            "powerscore_rating": ps.get("rating"),
            "fund_pct": fund.get("pct"),
            "fcf_yield": fund.get("fcf_yield"),
            "ev_ebitda": fund.get("ev_ebitda"),
            "entry": plan.get("entry"),
            "sl": plan.get("sl"),
            "tp": plan.get("tp"),
            "rr": plan.get("rr"),
            "corp_override": fund.get("corp_override"),
            "bandar_1d": (sb.get("bandar_1d") or {}).get("status") if sb.get("available") else None,
            "bandar_7d": (sb.get("bandar_7d") or {}).get("status") if sb.get("available") else None,
            "fnet": (sb.get("orderbook") or {}).get("fnet") if sb.get("available") else None,
        }

    items = await asyncio.gather(*(_one(s) for s in uniq))
    return _json_safe({"ok": True, "items": list(items)})


@app.get("/api/accuracy")
async def get_accuracy() -> dict[str, Any]:
    records = await asyncio.to_thread(accuracy_tracker._load_records)
    summary = await asyncio.to_thread(accuracy_tracker.summarize_accuracy, records)
    return _json_safe({"ok": True, "summary": summary, "records": list(reversed(records[-50:]))})


@app.post("/api/accuracy/record")
async def post_accuracy_record(body: AccuracyRecordPayload) -> dict[str, Any]:
    sym = body.ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    rec_date = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d")
    rec = await asyncio.to_thread(
        accuracy_tracker.record_recommendation,
        sym,
        rec_date,
        body.verdict,
        body.entry_low,
        body.entry_high,
        body.sl,
        body.tp1,
        body.tp2,
        regime=body.regime,
        power_score=body.power_score,
        notes=body.notes,
    )
    return _json_safe({"ok": True, "record": rec})


@app.post("/api/accuracy/evaluate")
async def post_accuracy_evaluate() -> dict[str, Any]:
    records = await asyncio.to_thread(accuracy_tracker.evaluate_recommendations)
    summary = await asyncio.to_thread(accuracy_tracker.summarize_accuracy, records)
    return _json_safe({"ok": True, "summary": summary, "records": list(reversed(records[-50:]))})


@app.get("/api/profile")
async def get_profile(chat_id: str = Query(dm.DEFAULT_PROFILE_ID)) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    modal = await asyncio.to_thread(store_watchlist.get_modal, cid)
    syariah = await asyncio.to_thread(store_profile.get_syariah_mode, cid)
    ui_state = await asyncio.to_thread(store_profile.get_desktop_ui_state, cid)
    clock_txt, market_status = dh.market_clock_text()
    refresh_val = str(ui_state.get("refresh") or store_profile.DEFAULT_DESKTOP_UI_STATE["refresh"])
    return {
        "chat_id": cid,
        "modal": int(modal or dh.DEFAULT_MODAL_RP),
        "syariah": bool(syariah),
        "refresh": refresh_val,
        "refresh_options": dict(dh.VALID_REFRESH_MIN),
        "default_profile_id": dm.DEFAULT_PROFILE_ID,
        "default_modal": dh.DEFAULT_MODAL_RP,
        "recent_tickers": list(ui_state.get("recent_tickers") or []),
        "clock": clock_txt,
        "market_status": market_status,
    }


@app.post("/api/profile")
async def save_profile(body: ProfilePayload) -> dict[str, Any]:
    cid = body.chat_id.strip() or dm.DEFAULT_PROFILE_ID
    modal = max(0, int(body.modal))
    if modal > 0:
        await asyncio.to_thread(store_watchlist.set_modal, cid, modal)
    await asyncio.to_thread(store_profile.set_syariah_mode, cid, bool(body.syariah))
    if body.refresh is not None:
        ui_state = await asyncio.to_thread(store_profile.set_desktop_ui_state, cid, refresh=body.refresh)
    else:
        ui_state = await asyncio.to_thread(store_profile.get_desktop_ui_state, cid)
    return {
        "ok": True,
        "chat_id": cid,
        "modal": modal or dh.DEFAULT_MODAL_RP,
        "syariah": bool(body.syariah),
        "refresh": str(ui_state.get("refresh") or store_profile.DEFAULT_DESKTOP_UI_STATE["refresh"]),
        "refresh_options": dict(dh.VALID_REFRESH_MIN),
    }


@app.get("/api/market")
async def get_market() -> dict[str, Any]:
    global _last_regime_cache
    pulse = await asyncio.to_thread(dm.fetch_market_pulse, include_idx=True, include_news=True)
    reg = (pulse.get("ihsg") or {}).get("regime")
    if isinstance(reg, dict):
        _last_regime_cache = reg
    pulse["stale_age_text"] = dh.format_stale_age(pulse.get("index_stale_age"))
    clock_txt, market_status = dh.market_clock_text()
    pulse["clock"] = clock_txt
    pulse["market_status"] = market_status
    try:
        top_q = await asyncio.to_thread(dm.load_sinyal_rows, "BELI", quality_only=True, limit=5)
        q_rows = list((top_q or {}).get("rows") or [])
        if len(q_rows) >= 3:
            pulse["top_picks"] = {"quality_filtered": True, "rows": q_rows[:5]}
        else:
            top_all = await asyncio.to_thread(dm.load_sinyal_rows, "BELI", quality_only=False, limit=5)
            pulse["top_picks"] = {
                "quality_filtered": False,
                "rows": list((top_all or {}).get("rows") or [])[:5],
            }
    except Exception:
        pulse["top_picks"] = {"quality_filtered": False, "rows": []}
    return _json_safe(pulse)


@app.get("/api/watchlist")
async def get_watchlist(
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
    modal: float = Query(float(dh.DEFAULT_MODAL_RP)),
    syariah: bool = Query(False),
) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    tickers = await asyncio.to_thread(store_watchlist.get_watchlist, cid)
    alerts_map = await asyncio.to_thread(store_watchlist.get_alerts, cid)
    rows = await asyncio.to_thread(
        dm.evaluate_watchlist_rows,
        tickers,
        modal,
        market_regime=_last_regime_cache,
        syariah_only=syariah,
        alerts_map=alerts_map,
    )
    return _json_safe({"ok": True, "rows": rows, "total": len(rows)})


@app.post("/api/watchlist")
async def add_watchlist(body: WatchPayload) -> dict[str, Any]:
    sym = body.ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    cid = body.chat_id.strip() or dm.DEFAULT_PROFILE_ID
    ok, items = await asyncio.to_thread(store_watchlist.watchlist_add, cid, sym)
    return {"ok": ok, "ticker": sym, "count": len(items)}


@app.delete("/api/watchlist/{ticker}")
async def delete_watchlist(
    ticker: str,
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    ok, items = await asyncio.to_thread(store_watchlist.watchlist_remove, cid, sym)
    return {"ok": ok, "ticker": sym, "count": len(items)}


@app.get("/api/sinyal")
async def get_sinyal(
    signal: str = Query("ALL"),
    sector: str = Query(""),
    q: str = Query(""),
    quality: bool = Query(False),
    syariah: bool = Query(False),
    limit: int = Query(200),
) -> dict[str, Any]:
    sec_arg = None if (not sector or sector == "Semua Sektor") else sector
    res = await asyncio.to_thread(
        dm.load_sinyal_rows,
        signal,
        quality_only=quality,
        syariah_only=syariah,
        sector=sec_arg,
        search=q or None,
        limit=max(10, min(500, limit)),
    )
    for row in (res or {}).get("rows") or []:
        prec = pro_analyzer.score_candidate_precision(row)
        row["precision_score"] = prec["score"]
        row["precision_grade"] = prec["grade"]
        row["precision_tone"] = prec["tone"]
        row["precision_note"] = prec["note"]
    return _json_safe(res)


@app.post("/api/scan")
async def trigger_scan(mode: str = Query("full")) -> dict[str, Any]:
    clean_mode = "fast" if str(mode or "").strip().lower() == "fast" else "full"
    target_syms = universe.main_board_codes() if clean_mode == "fast" else None
    est_total = len(target_syms) if target_syms is not None else len(universe.codes())
    started_at = dt.datetime.now(dt.timezone.utc).timestamp()
    acquired, st = dm.try_begin_market_scan(started_at=started_at, mode=clean_mode, total=est_total)
    if not acquired:
        return {
            "ok": True,
            "running": True,
            "mode": st.get("mode", clean_mode),
            "total": st.get("total", est_total),
            "started_at": st.get("started_at"),
            "message": "Scan BEI sedang berjalan.",
        }

    def _run() -> None:
        try:
            res = dm.run_full_market_scan(
                force=True,
                _lock_acquired=True,
                symbols=target_syms,
                mode=clean_mode,
            )
            with dm._SCAN_LOCK:
                dm._SCAN_STATE["last_result"] = _json_safe(res)
        except Exception as exc:
            dm.finish_market_scan(error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    label = (
        f"Scan cepat {est_total} emiten Papan Utama dimulai…"
        if clean_mode == "fast"
        else f"Scan penuh ~{est_total} emiten BEI dimulai (3 batch paralel)…"
    )
    return {
        "ok": True,
        "running": True,
        "mode": clean_mode,
        "total": est_total,
        "started_at": started_at,
        "message": label,
    }


@app.get("/api/scan/status")
async def get_scan_status() -> dict[str, Any]:
    return _json_safe(dm.get_scan_state())


_SCAN_STREAM_INTERVAL_S = 0.8


@app.get("/api/scan/stream")
async def stream_scan_status(request: Request) -> StreamingResponse:
    """Server-Sent Events (SSE) untuk progres /scan secara live tanpa polling."""
    async def _gen():
        while True:
            if await request.is_disconnected():
                break
            st = _json_safe(dm.get_scan_state())
            yield f"event: status\ndata: {json.dumps(st, ensure_ascii=False)}\n\n"
            if not st.get("running"):
                break
            await asyncio.sleep(_SCAN_STREAM_INTERVAL_S)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/paket")
async def get_paket(
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
    modal: float = Query(float(dh.DEFAULT_MODAL_RP)),
    max_plans: int = Query(5),
    syariah: bool = Query(False),
) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    res = await asyncio.to_thread(
        dm.build_paket_picks,
        modal,
        chat_id=cid,
        max_plans=max(1, min(5, max_plans)),
        market_regime=_last_regime_cache,
        syariah_only=syariah,
    )
    plans = res.get("plans") or []
    res["order_ticket"] = dm.format_order_ticket_text(plans) if plans else ""
    # Tambahkan kandidat BELI terdekat agar transparan saat modal kecil / rezim defensif
    near: list[dict[str, Any]] = []
    try:
        top_beli = await asyncio.to_thread(dm.load_sinyal_rows, "BELI", syariah_only=syariah, limit=6)
        scale = float((_last_regime_cache or {}).get("exposure_scale") or 1.0)
        eff_budget = modal * scale
        for row in (top_beli or {}).get("rows") or []:
            px = dh._safe_float(row.get("price"))
            if not px or px <= 0:
                continue
            min_lot = round(px * 100.0 * 1.0015)
            # Perkiraan modal minimal agar 1 lot masuk batas 10-20% posisi
            min_modal_est = round(min_lot / max(0.05, 0.20 * scale))
            near.append({
                "ticker": row.get("ticker"),
                "name": row.get("name"),
                "sector": row.get("sector"),
                "price": px,
                "rel_pct": row.get("rel_pct"),
                "syariah": bool(row.get("syariah")),
                "min_lot_cost": min_lot,
                "fits_budget": eff_budget >= min_lot,
                "min_modal_est": min_modal_est,
            })
    except Exception:
        near = []
    res["near_candidates"] = near
    return _json_safe(res)


@app.get("/api/portfolio")
async def get_portfolio(chat_id: str = Query(dm.DEFAULT_PROFILE_ID)) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    rows = await asyncio.to_thread(
        dm.evaluate_portfolio_rows,
        cid,
        market_regime=_last_regime_cache,
    )
    total_cost = sum(float(r.get("cost_rp") or 0) for r in rows)
    total_value = sum(float(r.get("value_rp") or 0) for r in rows)
    total_pnl_rp = sum(float(r.get("pnl_rp") or 0) for r in rows)
    total_pnl_pct = (total_pnl_rp / total_cost * 100.0) if total_cost > 0 else 0.0
    total_risk_sl = 0.0
    for r in rows:
        sl = r.get("effective_sl")
        px = r.get("price") or r.get("avg_price")
        lots = int(r.get("lots") or 0)
        if sl and px and lots > 0:
            total_risk_sl += max(0.0, (float(px) - float(sl)) * lots * 100.0)
    return _json_safe({
        "ok": True,
        "rows": rows,
        "summary": {
            "total_cost": round(total_cost),
            "total_value": round(total_value),
            "total_pnl_rp": round(total_pnl_rp),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "total_risk_sl": round(total_risk_sl),
            "count": len(rows),
        },
    })


@app.post("/api/execution")
async def post_execution(body: ExecutionPayload) -> dict[str, Any]:
    cid = body.chat_id.strip() or dm.DEFAULT_PROFILE_ID
    try:
        res = await asyncio.to_thread(
            dh.record_desktop_execution,
            cid,
            body.ticker,
            body.side,
            body.lots,
            body.price,
            note=body.notes,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(status_code=400, detail=str(res["error"]))
    return _json_safe({
        "ok": True,
        "message": f"Eksekusi {body.side.upper()} {body.ticker.upper()} {body.lots} lot @ {dh._fmt_rp(body.price)} tersimpan.",
        "result": res,
    })


@app.delete("/api/execution/{exec_id}")
async def remove_execution(
    exec_id: int,
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    try:
        res = await asyncio.to_thread(store_portfolio.delete_execution, cid, exec_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _json_safe({
        "ok": True,
        "message": (
            f"Inputan {res['side']} {res['ticker']} {res['lots']} lot "
            f"@ {dh._fmt_rp(res['exec_price'])} dihapus (sisa {res['lots_left']} lot)."
        ),
        "result": res,
    })


@app.delete("/api/portfolio/{ticker}")
async def remove_portfolio_position(
    ticker: str,
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    sym = ticker.strip().upper()
    try:
        res = await asyncio.to_thread(store_portfolio.delete_real_position, cid, sym)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _json_safe({
        "ok": True,
        "message": (
            f"Posisi & {res['deleted_executions']} riwayat eksekusi {sym} dihapus."
        ),
        "result": res,
    })


@app.post("/api/execution/import")
async def import_executions(body: BulkImportPayload) -> dict[str, Any]:
    cid = body.chat_id.strip() or dm.DEFAULT_PROFILE_ID
    parsed, errors = analysis_enhancer.parse_bulk_executions(body.raw_text)
    imported = 0
    for item in parsed:
        try:
            res = await asyncio.to_thread(
                dh.record_desktop_execution,
                cid,
                item["ticker"],
                item["side"],
                item["lots"],
                item["price"],
                note=item["notes"],
            )
            if isinstance(res, dict) and res.get("error"):
                errors.append(f"{item['ticker']}: {res['error']}")
            else:
                imported += 1
        except Exception as exc:
            errors.append(f"{item['ticker']}: {exc}")
    return {"ok": imported > 0, "imported": imported, "errors": errors}


@app.get("/api/portfolio/benchmark")
async def get_portfolio_benchmark(
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
    days: int = Query(45),
) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    rows = await asyncio.to_thread(dm.evaluate_portfolio_rows, cid, market_regime=_last_regime_cache)
    res = await asyncio.to_thread(analysis_enhancer.compute_portfolio_vs_ihsg, rows, days=days)
    return _json_safe(res)


@app.get("/api/jurnal")
async def get_jurnal(chat_id: str = Query(dm.DEFAULT_PROFILE_ID)) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    res = await asyncio.to_thread(dm.build_journal_summary, cid)
    return _json_safe(res)


@app.get("/api/alerts")
async def get_alerts(chat_id: str = Query(dm.DEFAULT_PROFILE_ID)) -> dict[str, Any]:
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    raw = await asyncio.to_thread(store_watchlist.get_alerts, cid)
    items = [{"ticker": sym, "kinds": list(kinds or [])} for sym, kinds in sorted(raw.items())]
    return _json_safe({
        "ok": True,
        "items": items,
        "total": len(items),
        "available_kinds": list(dh.ALERT_KIND_ORDER),
    })


@app.post("/api/alerts")
async def save_alert(body: AlertPayload) -> dict[str, Any]:
    sym = body.ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    valid = [k for k in body.kinds if k in dh.ALERT_KIND_ORDER]
    if not valid:
        raise HTTPException(status_code=400, detail="Pilih minimal 1 kondisi alert.")
    cid = body.chat_id.strip() or dm.DEFAULT_PROFILE_ID
    saved = await asyncio.to_thread(store_watchlist.set_alert, cid, sym, set(valid))
    return {"ok": True, "ticker": sym, "kinds": sorted(saved)}


@app.delete("/api/alerts/{ticker}")
async def delete_alert(
    ticker: str,
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    ok = await asyncio.to_thread(store_watchlist.remove_alert, cid, sym)
    return {"ok": ok, "ticker": sym}


@app.post("/api/alerts/check")
async def check_alerts_now(chat_id: str = Query(dm.DEFAULT_PROFILE_ID)) -> dict[str, Any]:
    """Pindai semua alert teknikal aktif & ambang SL/TP1/Tesis posisi portofolio."""
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    raw_alerts = await asyncio.to_thread(store_watchlist.get_alerts, cid)
    porto_rows = await asyncio.to_thread(dm.evaluate_portfolio_rows, cid, market_regime=_last_regime_cache)
    triggered: list[dict[str, Any]] = []

    # 1. Cek pemicu SL / TP1 / TP / Tesis Gugur di portofolio nyata
    for r in porto_rows:
        sym = r.get("ticker")
        px = dh._safe_float(r.get("price"))
        sl = dh._safe_float(r.get("effective_sl"))
        tp1 = dh._safe_float(r.get("scale_out_tp1"))
        tp = dh._safe_float(r.get("tp"))
        th = str(r.get("thesis") or "").upper()
        if px and sl and px <= sl:
            triggered.append({
                "source": "portfolio", "ticker": sym, "severity": "danger",
                "title": f"CUT LOSS — {sym} menyentuh SL",
                "detail": f"Harga {dh._fmt_rp(px)} <= SL efektif {dh._fmt_rp(sl)} ({r.get('lots', 0)} lot).",
                "price": px,
            })
        elif px and tp and px >= tp:
            triggered.append({
                "source": "portfolio", "ticker": sym, "severity": "ok",
                "title": f"AMBIL PROFIT — {sym} mencapai TP",
                "detail": f"Harga {dh._fmt_rp(px)} >= Target TP {dh._fmt_rp(tp)}.",
                "price": px,
            })
        elif px and tp1 and px >= tp1:
            triggered.append({
                "source": "portfolio", "ticker": sym, "severity": "ok",
                "title": f"TP1 TERCAPAI — {sym}",
                "detail": f"Harga {dh._fmt_rp(px)} >= TP1 {dh._fmt_rp(tp1)} (pertimbangkan jual 50% / naikkan SL ke BEP).",
                "price": px,
            })
        elif th == "GUGUR":
            triggered.append({
                "source": "portfolio", "ticker": sym, "severity": "danger",
                "title": f"TESIS GUGUR — {sym}",
                "detail": "; ".join(r.get("reasons") or []) or "Tesis posisi melemah/gugur.",
                "price": px,
            })

    # 2. Cek alert teknikal terdaftar
    def _scan_one_alert(sym: str, kinds: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            df = datamod.fetch_history(sym, period="6mo")
            if df is None or len(df) < 30:
                return out
            ind = analysismod.compute_indicators(df)
            hits = alertsmod.match_kinds(alertsmod.check_triggers(ind), kinds)
            if hits:
                px = float(ind["close"].iloc[-1])
                labels = [alertsmod.KIND_LABEL.get(k, k) for k in sorted(hits)]
                out.append({
                    "source": "alert", "ticker": sym, "severity": "info",
                    "title": f"ALERT TERPICU — {sym}",
                    "detail": ", ".join(labels) + f" (Harga {dh._fmt_rp(px)})",
                    "price": px,
                })
        except Exception:
            pass
        return out

    for sym, kinds in sorted(raw_alerts.items()):
        if kinds:
            found = await asyncio.to_thread(_scan_one_alert, sym, list(kinds))
            triggered.extend(found)

    now_wib = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%d %b %Y %H:%M WIB")
    return _json_safe({
        "ok": True,
        "checked_at": now_wib,
        "alerts_checked": len(raw_alerts),
        "positions_checked": len(porto_rows),
        "triggered": triggered,
    })


@app.get("/api/cek/{ticker}")
async def get_cek(
    ticker: str,
    chat_id: str = Query(dm.DEFAULT_PROFILE_ID),
    modal: float = Query(float(dh.DEFAULT_MODAL_RP)),
    period: str = Query("1y"),
    bandar: bool = Query(False),
) -> dict[str, Any]:
    sym = ticker.strip().upper().replace(".JK", "")
    if not sym:
        raise HTTPException(status_code=400, detail="Kode ticker kosong.")
    cid = chat_id.strip() or dm.DEFAULT_PROFILE_ID
    try:
        detail = await asyncio.to_thread(
            dm.inspect_ticker_detail,
            sym,
            modal,
            chat_id=cid,
            period=period,
            market_regime=_last_regime_cache,
            include_bandar=bandar,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ind = (detail.get("res") or {}).get("ind")
    candles = _extract_candles(ind, limit=260)
    plan = detail.get("plan") or {}
    payload = _json_safe(detail)
    raw_name = str((detail.get("fund_raw") or {}).get("name") or "").strip()
    cur_name = str(payload.get("name") or "").strip()
    if raw_name and (len(raw_name) > len(cur_name) or cur_name.count("(") > cur_name.count(")")):
        payload["name"] = raw_name
    payload["candles"] = candles
    payload["poc_60d"] = _calc_poc_60d(candles)
    payload["peers"] = banding.pick_sector_peers(sym, detail.get("sector"), limit=4)
    snap_px = ((detail.get("res") or {}).get("snap") or {}).get("close")
    fv = analysis_enhancer.compute_fair_value(
        snap_px,
        detail.get("fund_raw"),
        detail.get("fund"),
        detail.get("sector"),
    )
    payload["fair_value"] = _json_safe(fv)
    payload["pro_analysis"] = _json_safe(pro_analyzer.analyze_pro(detail, fair_value=fv))
    payload["simple_summary"] = _json_safe(analysis_enhancer.build_simple_summary(detail, fv))
    news_corp = analysis_enhancer.detect_corp_action_news(detail.get("news"))
    payload["news_corp_alerts"] = _json_safe(news_corp)
    payload["prebuy_checklist"] = _json_safe(analysis_enhancer.build_prebuy_checklist(detail, news_corp))
    payload["plan_levels"] = dh.format_plan_levels(plan)
    payload["pullback_sub"] = dh.format_pullback_sub(plan)
    payload["order_ticket"] = dm.format_order_ticket_text([plan]) if plan.get("ok") else ""
    entry_v = dh._safe_float(plan.get("entry"))
    sl_v = dh._safe_float(plan.get("sl"))
    tp_v = dh._safe_float(plan.get("tp"))
    pb_v = dh._safe_float(plan.get("entry_pullback"))
    lots_v = int(plan.get("lots") or 0)
    min_lot_cost = dh._safe_float(plan.get("min_budget") or plan.get("lot_value"))
    risk_per_lot = ((entry_v - sl_v) * 100.0) if (entry_v and sl_v and entry_v > sl_v) else None
    payload["plan_display"] = _json_safe({
        "ok": bool(plan.get("ok")),
        "entry": dh._fmt_rp(entry_v) if entry_v else "-",
        "entry_sub": (
            f"Pullback {dh._fmt_rp(pb_v)}"
            + (f" · Chase >{dh._fmt_rp(plan.get('chase_above'))}" if plan.get("chase_above") else "")
            if pb_v
            else (payload["pullback_sub"] or "Limit beli")
        ),
        "sl": dh._fmt_rp(sl_v) if sl_v else "-",
        "sl_sub": (
            f"-{plan['sl_dist_pct']:.1f}%"
            + (f" · ATR {dh._fmt_rp(plan.get('atr'))}" if plan.get("atr") else "")
            if plan.get("sl_dist_pct") is not None
            else "Batas rugi"
        ),
        "tp": dh._fmt_rp(tp_v) if tp_v else "-",
        "tp_sub": (
            f"+{plan['net_tp_pct']:.1f}% net"
            + (f" · R:R 1:{plan['rr']:.1f}" if plan.get("rr") else "")
            if plan.get("net_tp_pct") is not None
            else "Target profit"
        ),
        "lots": f"{lots_v} lot" if (plan.get("ok") and lots_v > 0) else "0 lot (ditolak)",
        "lots_sub": (
            f"1 lot = 100 saham · {plan.get('pct_modal'):.1f}% modal"
            if (plan.get("ok") and plan.get("pct_modal") is not None)
            else (plan.get("skip_reason") or plan.get("reason") or "Melebihi batas risiko/rezim")
        ),
        "cost": (
            dh._fmt_rp(plan.get("buy_cost"))
            if (plan.get("ok") and plan.get("buy_cost"))
            else (f"{dh._fmt_rp(min_lot_cost)} / lot" if min_lot_cost else "-")
        ),
        "cost_sub": (
            "Termasuk fee beli 0,15%"
            if plan.get("ok")
            else (
                f"Min. 1 lot · Anggaran {dh._fmt_rp(plan.get('budget'))}"
                if plan.get("budget")
                else "Estimasi per 1 lot"
            )
        ),
        "risk": (
            dh._fmt_rp(plan.get("max_loss"))
            if (plan.get("ok") and plan.get("max_loss"))
            else (f"{dh._fmt_rp(risk_per_lot)} / lot" if risk_per_lot else "-")
        ),
        "risk_sub": (
            f"{plan['risk_pct']:.2f}% dari modal"
            if (plan.get("ok") and plan.get("risk_pct") is not None)
            else (
                f"Batas risiko {dh._fmt_rp(plan.get('risk_budget'))}"
                if plan.get("risk_budget")
                else "Potensi rugi ke SL"
            )
        ),
    })
    return payload

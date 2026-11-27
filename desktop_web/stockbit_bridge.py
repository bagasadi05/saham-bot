"""Bridge opt-in lokal ke Stockbit MCP (`stdio` JSON-RPC) untuk SahamBot Modern Desktop.

Catatan arsitektur & keamanan:
- **Default OFF** (`SAHAMBOT_STOCKBIT_MCP=1` wajib diaktifkan secara lokal).
- **Murni read-only:** hanya memanggil `quote`, `orderbook`, dan `bandar_detector`.
- **Tidak menyimpan kredensial:** sesi berasal dari `stockbit-auth login` milik pengguna lokal.
- **Tidak menyentuh `core/` maupun bot Telegram**, sehingga CI & seluruh tes tetap 100% offline.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

DEFAULT_MCP_SCRIPT = str(
    Path(__file__).resolve().parent.parent.parent / "stockbit-mcp" / "dist" / "bin" / "stockbit-mcp.js"
)
_CACHE_TTL_FAST = 30.0
_CACHE_TTL_BANDAR = 120.0
_CACHE_TTL_KEYSTATS = 600.0


def is_enabled() -> bool:
    """Aktif hanya jika SAHAMBOT_STOCKBIT_MCP=1 dan skrip MCP lokal tersedia."""
    flag = os.environ.get("SAHAMBOT_STOCKBIT_MCP", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    script = get_script_path()
    return bool(script and Path(script).is_file() and shutil.which("node"))


def get_script_path() -> str:
    return os.environ.get("STOCKBIT_MCP_PATH", DEFAULT_MCP_SCRIPT).strip()


class _StockbitMcpClient:
    """Klien stdio JSON-RPC persisten & thread-safe untuk stockbit-mcp."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}

    def _reader_loop(self, stream: Any, q: queue.Queue[str | None]) -> None:
        try:
            for line in stream:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self.close()
        script = get_script_path()
        env = dict(os.environ)
        env.setdefault("STOCKBIT_NO_UPDATE_CHECK", "1")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._lines = queue.Queue()
        self._proc = subprocess.Popen(["node", script], **kwargs)
        threading.Thread(
            target=self._reader_loop,
            args=(self._proc.stdout, self._lines),
            daemon=True,
        ).start()
        # Handshake MCP initialize
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sahambot-desktop", "version": "1.0"},
            },
            timeout=10.0,
        )
        self._notify("notifications/initialized", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n"
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict[str, Any], timeout: float = 18.0) -> dict[str, Any]:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Proses stockbit-mcp tidak aktif.")
        self._req_id += 1
        rid = self._req_id
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n"
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

        deadline = time.time() + timeout
        while True:
            rem = deadline - time.time()
            if rem <= 0:
                raise TimeoutError(f"Timeout menunggu respons MCP {method}")
            try:
                line = self._lines.get(timeout=min(rem, 1.0))
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Koneksi stdout stockbit-mcp tertutup.")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result") or {}

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        ttl: float = _CACHE_TTL_FAST,
        timeout: float = 18.0,
    ) -> dict[str, Any] | None:
        cache_key = (name, json.dumps(arguments, sort_keys=True))
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < ttl:
            return cached[1]
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) < ttl:
                return cached[1]
            try:
                self._ensure_started()
                res = self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
            except Exception:
                self.close()
                return None
        parsed = _extract_tool_payload(res)
        if parsed is not None:
            self._cache[cache_key] = (time.time(), parsed)
        return parsed

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


_client = _StockbitMcpClient()
atexit.register(_client.close)


def _extract_tool_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Parse payload JSON dari blok `content[0].text` respons MCP `tools/call`."""
    if not isinstance(result, dict) or result.get("isError"):
        return None
    content = result.get("content") or []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            raw = item.get("text") or ""
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    if obj.get("success") is False:
                        return None
                    return obj.get("data") if isinstance(obj.get("data"), dict) else obj
            except json.JSONDecodeError:
                continue
    return None


def _simplify_orderbook(ob: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ob, dict):
        return None

    def _rows(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in (items or [])[:10]:
            try:
                px = float(it.get("price") or 0)
                # Volume orderbook Stockbit dalam lembar -> konversi ke lot (/100)
                vol_shares = float(it.get("volume") or 0)
                lots = round(vol_shares / 100.0)
                que = int(float(it.get("que_num") or 0))
                if px > 0:
                    out.append({"price": px, "lots": lots, "que": que})
            except (TypeError, ValueError):
                continue
        return out

    tot = ob.get("total_bid_offer") or {}
    return {
        "bids": _rows(ob.get("bid")),
        "offers": _rows(ob.get("offer")),
        "bid_pct": tot.get("bid_percent"),
        "total_bid_lot": (tot.get("bid") or {}).get("lot"),
        "total_offer_lot": (tot.get("offer") or {}).get("lot"),
        "fnet": ob.get("fnet"),
        "fbuy": ob.get("fbuy"),
        "fsell": ob.get("fsell"),
    }


def _simplify_bandar(bd: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(bd, dict):
        return None
    det = bd.get("stockbitBandarDetector") or {}
    conc = bd.get("concentration") or {}

    def _brokers(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in (items or [])[:5]:
            out.append({
                "code": it.get("code") or "-",
                "name": it.get("name") or "",
                "type": it.get("investorType") or "",
                "lots": it.get("netLots"),
                "value": it.get("netValueIdr"),
                "avg": round(float(it.get("avgPrice")), 1) if it.get("avgPrice") else None,
            })
        return out

    return {
        "from": bd.get("from"),
        "to": bd.get("to"),
        "status": (det.get("top3") or {}).get("accdist") or det.get("broker_accdist") or "Netral",
        "avg_status": (det.get("avg") or {}).get("accdist") or "",
        "average": round(float(det.get("average")), 1) if det.get("average") else None,
        "buyers_count": conc.get("buyersListed") or det.get("total_buyer"),
        "sellers_count": conc.get("sellersListed") or det.get("total_seller"),
        "top3_buy_share": round(float(conc.get("top3BuyerShare") or 0) * 100, 1),
        "top3_sell_share": round(float(conc.get("top3SellerShare") or 0) * 100, 1),
        "top_buyers": _brokers(bd.get("topAccumulators")),
        "top_sellers": _brokers(bd.get("topDistributors")),
    }


_KEYSTAT_WANT: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pe_ttm", "PE TTM", ("current pe ratio (ttm)",)),
    ("pe_fwd", "Forward PE", ("forward pe ratio",)),
    ("pbv", "PBV", ("current price to book value", "price to book value")),
    ("roe", "ROE (TTM)", ("return on equity (ttm)",)),
    ("roa", "ROA (TTM)", ("return on assets (ttm)",)),
    ("npm", "NPM (Q)", ("net profit margin (quarter)",)),
    ("nim", "NIM", ("net interest margin (nim)",)),
    ("casa", "CASA", ("casa ratio",)),
    ("coc", "Cost of Credit", ("cost of credit",)),
    ("rev_yoy", "Omzet YoY", ("revenue (quarter yoy growth)",)),
    ("ni_yoy", "Laba YoY", ("net income (quarter yoy growth)",)),
    ("div_yield", "Div. Yield", ("dividend yield",)),
)


def _simplify_keystats(ks: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(ks, dict):
        return []
    flat: dict[str, str] = {}
    for sec in ks.get("closure_fin_items_results") or []:
        for it in sec.get("fin_name_results") or []:
            nm = str(it.get("fitem_name") or "").strip().lower()
            val = str(it.get("fitem_value") or "").strip()
            if nm and val and val != "-":
                flat[nm] = val
    out: list[dict[str, str]] = []
    for key, label, aliases in _KEYSTAT_WANT:
        for al in aliases:
            if al in flat:
                out.append({"key": key, "label": label, "value": flat[al]})
                break
    return out


def fetch_stockbit_snapshot(ticker: str) -> dict[str, Any]:
    """Ambil snapshot read-only Stockbit (quote, orderbook 10L, bandar 1H & 7H, keystats)."""
    sym = ticker.strip().upper().replace(".JK", "")
    if not is_enabled():
        return {
            "available": False,
            "symbol": sym,
            "reason": "Bridge Stockbit MCP nonaktif (set SAHAMBOT_STOCKBIT_MCP=1 untuk mengaktifkan lokal).",
        }
    quote = _client.call_tool("quote", {"symbol": sym}, ttl=_CACHE_TTL_FAST)
    orderbook = _client.call_tool("orderbook", {"symbol": sym}, ttl=_CACHE_TTL_FAST)
    bd_1d = _client.call_tool(
        "bandar_detector",
        {"symbol": sym, "period": "LATEST", "resolve_names": True, "top": 5},
        ttl=_CACHE_TTL_BANDAR,
    )
    bd_7d = _client.call_tool(
        "bandar_detector",
        {"symbol": sym, "period": "LAST_7_DAYS", "resolve_names": True, "top": 5},
        ttl=_CACHE_TTL_BANDAR,
    )
    ks_raw = _client.call_tool("keystats", {"symbol": sym}, ttl=_CACHE_TTL_KEYSTATS)
    if not any((quote, orderbook, bd_1d, bd_7d, ks_raw)):
        return {
            "available": False,
            "symbol": sym,
            "reason": "Sesi Stockbit MCP tidak terjangkau atau perlu login ulang (`stockbit-auth login`).",
        }
    return {
        "available": True,
        "symbol": sym,
        "quote": quote,
        "orderbook": _simplify_orderbook(orderbook),
        "bandar_1d": _simplify_bandar(bd_1d),
        "bandar_7d": _simplify_bandar(bd_7d),
        "keystats": _simplify_keystats(ks_raw),
    }

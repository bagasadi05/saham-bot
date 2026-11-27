"""SahamBot Modern Desktop — Aplikasi Windows Native (App Window + System Tray + FastAPI).

Jalankan:
    .venv\\Scripts\\python.exe desktop_modern.py
atau klik ganda `run_desktop_modern.bat`.

Membuka jendela aplikasi mandiri (tanpa address bar/tab browser) memakai Chrome/Edge
App Mode yang terhubung ke backend FastAPI lokal (`desktop_web/api.py`).
Jika jendela ditutup (`×`), server tetap berjalan ringan di background (System Tray
Windows) sehingga proses `/scan` tidak terputus dan aplikasi terbuka instan saat
diklik lagi. Untuk mematikan total, klik kanan ikon SahamBot di System Tray -> Keluar.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "temp" / "desktop_modern.log"
DEFAULT_PORT = 18765


def _log(msg: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn  # noqa: E402

from core.desktop_helpers import create_tray_icon_image  # noqa: E402
from desktop_web import stockbit_bridge  # noqa: E402
from desktop_web.api import app as fastapi_app  # noqa: E402


def _is_sahambot_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health",
            timeout=0.6,
        ) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            return data.get("app") == "sahambot-desktop"
    except Exception:
        return False


def _pick_port(preferred: int = DEFAULT_PORT) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _lan_enabled() -> bool:
    return os.environ.get("SAHAMBOT_LAN", "").strip().lower() in ("1", "true", "yes", "on")


def _start_server(port: int) -> uvicorn.Server:
    lan_wanted = _lan_enabled()
    host = "127.0.0.1"
    if lan_wanted:
        # Mode LAN hanya boleh aktif bila PIN sudah di-set; jika belum, fallback ke localhost.
        try:
            from desktop_web.api import _load_pin_hash

            if _load_pin_hash():
                host = "0.0.0.0"
            else:
                os.environ["SAHAMBOT_LAN"] = "0"
                print(
                    "[PERINGATAN] SAHAMBOT_LAN=1 tetapi PIN belum di-set — server dikunci ke 127.0.0.1. "
                    "Set PIN lewat ikon gembok di UI (dari PC) untuk membuka akses LAN/HP.",
                    file=sys.stderr,
                )
        except Exception:
            host = "127.0.0.1"
    os.environ["SAHAMBOT_ACTIVE_PORT"] = str(port)
    cfg = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


def _find_chromium_app_browser() -> str | None:
    """Cari Chrome atau Edge untuk membuka jendela aplikasi mandiri (--app)."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LocalAppData", "")
    candidates = [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _launch_app_window(browser: str | None, url: str) -> subprocess.Popen | None:
    if not browser:
        webbrowser.open(url)
        return None
    profile_dir = ROOT / "temp" / "desktop_app_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--new-window",
        "--window-size=1360,860",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-extensions",
    ]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL
        kwargs["startupinfo"] = si
    return subprocess.Popen(cmd, **kwargs)


def _run_with_tray(browser: str | None, url: str) -> bool:
    """Jalankan ikon System Tray agar server tetap hidup di background saat jendela ditutup."""
    try:
        import pystray
    except Exception:
        return False

    def _on_open(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        _log("Membuka ulang jendela SahamBot dari System Tray.")
        _launch_app_window(browser, url)

    def _on_browser(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        webbrowser.open(url)

    def _on_quit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        _log("Keluar Total dipilih dari System Tray.")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Buka SahamBot", _on_open, default=True),
        pystray.MenuItem("Buka di Browser Biasa", _on_browser),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Keluar Total (Matikan Server)", _on_quit),
    )
    icon = pystray.Icon(
        "SahamBotModern",
        create_tray_icon_image(64),
        "SahamBot — Terminal BEI (Aktif di Background)",
        menu,
    )
    _log("System Tray aktif — menutup jendela tidak mematikan server.")
    icon.run()
    return True


def main() -> int:
    try:
        browser = _find_chromium_app_browser()
        if _is_sahambot_running(DEFAULT_PORT):
            url = f"http://127.0.0.1:{DEFAULT_PORT}/"
            _log(f"Instance sudah aktif di {url}; membuka jendela baru secara instan.")
            _launch_app_window(browser, url)
            return 0

        port = _pick_port(DEFAULT_PORT)
        url = f"http://127.0.0.1:{port}/"
        _log(f"Memulai server FastAPI di {url} (browser={browser})")
        server = _start_server(port)
        if not server.started:
            _log("Server FastAPI gagal start dalam 10 detik.")
            return 1

        try:
            proc = _launch_app_window(browser, url)
            if proc:
                _log(f"Browser app diluncurkan PID={proc.pid}")
            # Tetap hidup di System Tray Windows saat jendela ditutup (×)
            if not _run_with_tray(browser, url):
                if proc is not None:
                    proc.wait()
                else:
                    while True:
                        time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            stockbit_bridge._client.close()
            server.should_exit = True
            _log("Server FastAPI & bridge dihentikan.")
        return 0
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

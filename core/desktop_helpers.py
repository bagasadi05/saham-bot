"""Helper murni aplikasi SahamBot Desktop (tanpa ketergantungan ``tkinter``).

Modul ini menampung seluruh fungsi pemformatan angka/teks, logika UI/UX murni
(jam bursa, spinner, countdown, rich-text classifier, cross-link sektor,
format kartu rencana beli), pembuat ikon tray (PIL), dan pencatatan eksekusi
SQLite agar dapat diuji di lingkungan CI *headless* tanpa ``python3-tk``.
"""
from __future__ import annotations

from datetime import datetime
import html
import re
from typing import Any

from PIL import Image, ImageDraw

from core import desktop_monitor as dm
from core import marketcal
from core import store_portfolio
from core import store_profile

DESKTOP_CHAT_ID = dm.DEFAULT_PROFILE_ID
APP_ID = "SahamBot.Desktop.Terminal.2"
DEFAULT_MODAL_RP = 10_000_000
VALID_REFRESH_MIN = dict(store_profile.DESKTOP_REFRESH_OPTIONS)

ALERT_KIND_ORDER = (
    "jump",
    "drop",
    "breakout",
    "breakdown",
    "oversold",
    "overbought",
    "ma_golden",
    "ma_death",
    "vol_spike",
)

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# Alias nama sektor IDX-IC (Inggris, dipakai grafik rotasi) ke sektor Indonesia (snapshot /scan)
SECTOR_ALIAS_IDX_IC = {
    "financials": "keuangan",
    "energy": "energi",
    "basic materials": "bahan baku",
    "industrials": "industri",
    "consumer cyclicals": "konsumen siklikal",
    "consumer non-cyclicals": "konsumen primer",
    "technology": "teknologi",
    "infrastructures": "infrastruktur",
    "transportation & logistics": "transportasi",
    "transportation": "transportasi",
    "healthcare": "kesehatan",
    "properties & real estate": "properti",
    "real estate": "properti",
    "utilities": "utilitas",
}


# ---------------------------------------------------------------------------
# Formatter & Utilitas Dasar
# ---------------------------------------------------------------------------

def _fmt_rp(value: Any) -> str:
    """Format nominal Rupiah bersih (``Rp1.500.000`` / ``-Rp1.500.000`` / ``-``)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:  # NaN check
        return "-"
    sign = "-" if num < 0 else ""
    rounded = round(abs(num))
    formatted = f"{rounded:,}".replace(",", ".")
    return f"{sign}Rp{formatted}"


def _fmt_pct(value: Any, *, signed: bool = True) -> str:
    """Format persentase dengan 2 desimal (``+1.23%`` / ``-2.50%`` / ``-``)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if signed:
        return f"{num:+.2f}%"
    return f"{num:.2f}%"


def _fmt_num(value: Any, decimals: int = 0) -> str:
    """Format angka biasa dengan pemisah ribuan titik untuk angka non-Rupiah."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if decimals <= 0:
        return f"{round(num):,}".replace(",", ".")
    return f"{num:,.{decimals}f}".replace(",", ".")


def _strip_html(text: Any) -> str:
    """Bersihkan tag HTML sederhana dan decode entitas HTML."""
    raw = str(text or "")
    unescaped = html.unescape(raw)
    cleaned = re.sub(r"<[^>]+>", "", unescaped)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _fmt_arrow_pct(value: Any) -> str:
    """Format persen dengan penanda arah ``▲``/``▼`` untuk aksesibilitas visual."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    if num != num:
        return "-"
    if num > 0:
        return f"▲ +{num:.2f}%"
    if num < 0:
        return f"▼ {num:.2f}%"
    return "0.00%"


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        num = float(value)
        return num if num == num else default
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Konversi aman ke int (``"a"``/``None`` → ``default``), tidak pernah melempar."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_sort_key(raw: str, kind: str) -> tuple[int, Any]:
    """Konversi string sel Treeview menjadi kunci pengurutan yang konsisten."""
    txt = str(raw or "").strip()
    if txt in ("", "-", "NA", "tidak tersedia"):
        return (1, 0.0)
    if kind == "num":
        cleaned = (
            txt.replace("▲", "")
            .replace("▼", "")
            .replace("Rp", "")
            .replace("%", "")
            .replace("x", "")
            .replace("+", "")
            .replace(" ", "")
        )
        if cleaned.count(".") >= 1 and "," not in cleaned:
            parts = cleaned.split(".")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
                cleaned = "".join(parts)
        cleaned = cleaned.replace(",", ".")
        m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if m:
            try:
                return (0, float(m.group(0)))
            except ValueError:
                pass
        return (1, txt.lower())
    return (0, txt.lower())


def _news_item_tag(item: dict[str, Any]) -> str:
    """Ambil label sentimen item berita (``POSITIF``/``NEGATIF``/``NETRAL``) dari skor ``sent``."""
    explicit = str(item.get("sentiment") or item.get("label") or "").strip().upper()
    if explicit:
        return explicit
    s = _safe_float(item.get("sent"))
    if s is None:
        return "NETRAL"
    if s >= 0.1:
        return "POSITIF"
    if s <= -0.1:
        return "NEGATIF"
    return "NETRAL"


# ---------------------------------------------------------------------------
# Helper Tampilan Murni (UI/UX)
# ---------------------------------------------------------------------------

def spinner_frame(i: int) -> str:
    """Frame animasi spinner untuk indikator proses berjalan di status bar."""
    return SPINNER_FRAMES[int(i) % len(SPINNER_FRAMES)]


def countdown_text(seconds: float) -> str:
    """Format sisa waktu auto-refresh ``MM:SS`` (atau ``H:MM:SS`` bila > 1 jam)."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def tab_label(base: str, count: int | None) -> str:
    """Label tab dengan badge counter, mis. ``⭐ Watchlist (5)``."""
    return f"{base} ({count})" if count is not None else base


def market_clock_text(now: datetime | None = None) -> tuple[str, str]:
    """Teks jam WIB + status bursa; status ``open``/``closed`` untuk pewarnaan UI."""
    n = now or marketcal.now_wib()
    status = "open" if marketcal.market_open(n) else "closed"
    label = "BURSA BUKA" if status == "open" else "BURSA TUTUP"
    return f"{n:%H:%M:%S} WIB · {label}", status


def busy_text(base: str, frame: int) -> str:
    """Label tombol saat prosesnya berjalan: spinner + teks asli + elipsis."""
    return f"{spinner_frame(frame)} {base}…"


def format_stale_age(age_sec: Any) -> str:
    """Format umur cache dalam detik menjadi teks ramah pengguna (mis. '63 mnt lalu')."""
    val = _safe_float(age_sec)
    if val is None or val < 0:
        return ""
    mins = int(round(val / 60.0))
    if mins < 1:
        return "baru saja"
    if mins < 60:
        return f"{mins} mnt lalu"
    hours = mins // 60
    rem = mins % 60
    return f"{hours} jam {rem} mnt lalu" if rem else f"{hours} jam lalu"


def classify_line(line: str) -> str:
    """Klasifikasi satu baris teks penjelasan menjadi tag gaya agar enak dibaca.

    Mengembalikan nama tag: ``heading``, ``good``, ``bad``, ``warn``, ``muted``,
    atau ``normal``.
    """
    s = str(line or "").strip()
    if not s:
        return "normal"
    if s.startswith("===") or (s.startswith("---") and s.endswith("---")) or s.startswith("💡"):
        return "heading"
    up = s.upper()
    if up.startswith("KONDISI PASAR:") or up.startswith("SENTIMEN BERITA PASAR:"):
        return "heading"
    if "[POSITIF]" in up:
        return "good"
    if "[NEGATIF]" in up:
        return "bad"
    if "[NETRAL]" in up:
        return "muted"
    if "⛔" in s or "VETO" in up or "HINDARI" in up:
        return "bad"
    if "⚠" in s or "TUNGGU" in up or "STALE" in up:
        return "warn"
    if "RISIKO" in up or "CATATAN KRITIS" in up:
        return "bad"
    if s.startswith("PRO") or "LAYAK" in up or "PENDUKUNG" in up:
        return "good"
    if s.startswith("🔎") or "TIDAK TERSEDIA" in up or s.startswith("Catatan Delay:"):
        return "muted"
    return "normal"


def inline_segments(line: str) -> list[tuple[str, str | None]]:
    """Pecah satu baris menjadi segmen ``(teks, 'b'|'i'|None)`` dari markup ``<b>``/``<i>``.

    Tag lain dihilangkan, entitas HTML di-decode — aman untuk teks dari core.
    """
    out: list[tuple[str, str | None]] = []
    pos = 0
    style: str | None = None
    for m in re.finditer(r"<(/?)([bi])>", str(line or "")):
        chunk = str(line)[pos:m.start()]
        if chunk:
            out.append((html.unescape(re.sub(r"<[^>]+>", "", chunk)), style))
        style = None if m.group(1) == "/" else m.group(2)
        pos = m.end()
    tail = str(line)[pos:]
    if tail:
        out.append((html.unescape(re.sub(r"<[^>]+>", "", tail)), style))
    return [(t, s) for t, s in out if t]


def hit_test_rects(
    rects: list[tuple[float, float, float, float, str]], x: float, y: float,
) -> str | None:
    """Kembalikan nama pada rect ``(x0, y0, x1, y1, nama)`` yang terkena titik ``(x, y)``."""
    for x0, y0, x1, y1, name in rects:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return None


def format_plan_levels(plan: Any) -> dict[str, str]:
    """Format level rencana beli (entry/SL/TP/lot/biaya/risiko) untuk kartu ringkasan.

    ``plan`` dengan ``ok=False``/bukan dict menghasilkan semua ``"-"`` (jujur: tidak ada angka).
    """
    keys = ("entry", "pullback", "sl", "tp", "lots", "cost", "risk")
    if not isinstance(plan, dict) or not plan.get("ok"):
        return {k: "-" for k in keys}
    return {
        "entry": _fmt_num(plan.get("entry"), 0),
        "pullback": _fmt_num(plan.get("entry_pullback"), 0),
        "sl": f"{_fmt_num(plan.get('sl'), 0)} ({_fmt_pct(plan.get('sl_dist_pct'))})",
        "tp": f"{_fmt_num(plan.get('tp'), 0)} (RR {float(plan.get('rr') or 0.0):.2f}x)",
        "lots": _fmt_num(plan.get("lots"), 0),
        "cost": _fmt_rp(plan.get("buy_cost")),
        "risk": f"{_fmt_rp(plan.get('max_loss'))} ({_fmt_pct(plan.get('max_loss_pct'), signed=False)})",
    }


def format_pullback_sub(plan: Any) -> str:
    """Subtitle Aturan 5 & 6 (peluang tersentuh vs ATR14 & masa berlaku 5 hari bursa)."""
    if not isinstance(plan, dict) or not plan.get("ok"):
        return "Batas limit beli"
    pb = plan.get("entry_pullback")
    if not pb:
        return "Batas limit beli"
    info = plan.get("pullback_info") if isinstance(plan.get("pullback_info"), dict) else None
    if info:
        lbl = str(info.get("touch_label") or "-")
        gap_atr = info.get("gap_atr")
        days = int(info.get("valid_days") or 5)
        atr_txt = f" ({float(gap_atr):.1f}×ATR)" if gap_atr is not None else ""
        return f"Pullback {_fmt_num(pb, 0)} · peluang {lbl}{atr_txt} · {days}h bursa"
    return f"Pullback MA20: {_fmt_num(pb, 0)} · berlaku 5h bursa"


def match_sector_option(name: str, options: list[str]) -> str | None:
    """Cocokkan nama sektor grafik IDX-IC ke opsi filter sinyal (alias, exact, lalu substring)."""
    n = str(name or "").strip()
    if not n:
        return None
    low = SECTOR_ALIAS_IDX_IC.get(n.lower(), n.lower())
    for opt in options:
        if opt == n or opt.lower() == low:
            return opt
    for opt in options:
        o = opt.lower()
        if low in o or o in low:
            return opt
    return None


def create_tray_icon_image(size: int = 64) -> Image.Image:
    """Buat ikon baki sistem SahamBot (ikon candlestick & garis tren hijau/biru)."""
    dim = max(16, int(size))
    img = Image.new("RGBA", (dim, dim), (9, 13, 22, 255))
    draw = ImageDraw.Draw(img)

    margin = max(2, dim // 12)
    radius = max(3, dim // 6)
    draw.rounded_rectangle(
        [margin, margin, dim - margin - 1, dim - margin - 1],
        radius=radius,
        fill=(19, 28, 49, 255),
        outline=(56, 189, 248, 255),
        width=max(1, dim // 24),
    )

    bars = [
        (0.26, 0.52, 0.72, (239, 68, 68, 255)),
        (0.49, 0.36, 0.62, (34, 197, 94, 255)),
        (0.72, 0.22, 0.50, (34, 197, 94, 255)),
    ]
    bar_w = max(2, int(dim * 0.12))
    wick_w = max(1, dim // 32)
    for cx_r, top_r, bot_r, color in bars:
        cx = int(dim * cx_r)
        top_y = int(dim * top_r)
        bot_y = int(dim * bot_r)
        draw.line(
            [(cx, max(margin + 2, top_y - bar_w)), (cx, min(dim - margin - 2, bot_y + bar_w))],
            fill=color, width=wick_w,
        )
        draw.rectangle(
            [cx - bar_w // 2, top_y, cx + bar_w // 2, bot_y],
            fill=color,
        )

    line_w = max(1, dim // 20)
    draw.line(
        [(int(dim * 0.20), int(dim * 0.74)),
         (int(dim * 0.48), int(dim * 0.48)),
         (int(dim * 0.80), int(dim * 0.20))],
        fill=(56, 189, 248, 255),
        width=line_w,
    )
    return img


def record_desktop_execution(
    chat_id: int | str,
    ticker: str,
    side: str,
    lots: int,
    price: float,
    *,
    trade_date: str | None = None,
    sl: float | None = None,
    tp: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Catat eksekusi BELI/JUAL manual Stockbit ke SQLite sesuai kontrak ``store_portfolio``."""
    sym = str(ticker or "").strip().upper().replace(".JK", "")
    sd = str(side or "").strip().upper()
    tdate = trade_date or datetime.now().strftime("%Y-%m-%d")
    src = f"desktop:{note.strip()[:40]}" if note and note.strip() else "desktop"
    if sd == "JUAL":
        return store_portfolio.record_execution_sell(
            chat_id, sym, int(lots), float(price), tdate, source=src,
        )
    return store_portfolio.record_execution_buy(
        chat_id, sym, int(lots), float(price), tdate, sl=sl, tp=tp, source=src,
    )

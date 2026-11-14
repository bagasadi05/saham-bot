"""Utilitas teks untuk pesan Telegram."""
from __future__ import annotations

import re

TELEGRAM_LIMIT = 4096


def rupiah(v) -> str:
    """Rupiah tanpa desimal, pemisah ribuan titik: Rp9.000 / — bila kosong."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"Rp{x:,.0f}".replace(",", ".")


def rupiah_desimal(v) -> str:
    """Rupiah dengan desimal hanya bila perlu: Rp1.080 / Rp1.944.000,50."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(x - round(x)) < 1e-9:
        return "Rp" + f"{x:,.0f}".replace(",", ".")
    return ("Rp" + f"{x:,.2f}".replace(",", "X")
            .replace(".", ",").replace("X", "."))

_PRE_OPEN = "<pre>"
_PRE_CLOSE = "</pre>"


def _pre_open(s: str) -> bool:
    """True jika blok <pre> masih terbuka di akhir potongan teks."""
    return s.count(_PRE_OPEN) > s.count(_PRE_CLOSE)


def _pre_state(state: bool, text: str) -> bool:
    """Status <pre> setelah membaca `text`, menghormati urutan tag.

    Berbeda dari hitung-count: teks `</pre><pre>` harus berakhir terbuka.
    """
    pos = 0
    while True:
        o = text.find(_PRE_OPEN, pos)
        c = text.find(_PRE_CLOSE, pos)
        if o < 0 and c < 0:
            return state
        if c < 0 or (0 <= o < c):
            state = True
            pos = o + len(_PRE_OPEN)
        else:
            state = False
            pos = c + len(_PRE_CLOSE)


def _fit(text: str, start: int, budget: int) -> int:
    """Akhir potongan (<= budget karakter) tanpa memotong tag di tengah."""
    end = min(len(text), start + max(1, budget))
    lt = text.rfind("<", start, end)
    if lt >= 0 and ">" not in text[lt:end]:
        end = lt
    return max(end, start + 1)


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Potong pesan panjang di batas baris sambil menjaga tag <pre> valid.

    Jaminan:
    - setiap potongan <= limit karakter (Telegram menolak > 4096);
    - tag <pre> selalu seimbang di tiap potongan: blok yang terpotong ditutup
      di akhir potongan dan dibuka lagi di potongan berikutnya;
    - tag tidak pernah terbelah di tengah;
    - baris yang lebih panjang dari limit dipotong paksa (aman-tag).
    """
    if limit <= len(_PRE_OPEN) + len(_PRE_CLOSE):
        raise ValueError("limit terlalu kecil untuk tag <pre>")
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0
    pre = False  # status <pre> di akhir isi buf

    def flush() -> None:
        nonlocal buf, buf_len, pre
        if buf:
            parts.append("\n".join(buf) + (_PRE_CLOSE if pre else ""))
            buf, buf_len, pre = [], 0, False

    for line in text.split("\n"):
        after = _pre_state(pre, line)
        extra = (1 if buf else 0) + (len(_PRE_CLOSE) if after else 0)
        if buf_len + len(line) + extra <= limit:
            buf.append(line)
            buf_len += len(line) + (1 if len(buf) > 1 else 0)
            pre = after
            continue

        # baris tidak muat: tutup buf, ingat apakah kita masih di dalam <pre>
        state_at_cut = pre
        flush()

        if len(line) + (len(_PRE_CLOSE) if _pre_state(state_at_cut, line) else 0) <= limit:
            buf = ([_PRE_OPEN] if state_at_cut else []) + [line]
            buf_len = len("\n".join(buf))
            pre = _pre_state(state_at_cut, line)
            continue

        # baris ekstrem: potong paksa, jaga status <pre> lintas potongan
        idx = 0
        state = state_at_cut
        while idx < len(line):
            avail = limit - (len(_PRE_OPEN) if state else 0) - len(_PRE_CLOSE)
            end = _fit(line, idx, max(1, avail))
            piece = line[idx:end]
            after = _pre_state(state, piece)
            chunk = (_PRE_OPEN if state else "") + piece
            if end >= len(line):
                buf = [chunk]
                buf_len = len(chunk)
                pre = after
            else:
                parts.append(chunk + (_PRE_CLOSE if after else ""))
            state = after
            idx = end

    flush()
    return parts


def parse_rupiah(tok: str) -> float | None:
    """Terima '5000000', '5.000.000', '5jt', '1,5jt', '500rb' -> rupiah.

    Return None bila bukan angka yang masuk akal -- pemanggil membalas contoh
    perintah, bukan menebak nominal.
    """
    s = (tok or "").strip().lower().replace("rp", "").replace(" ", "")
    if not s:
        return None
    mult = 1.0
    for suf, m in (("juta", 1e6), ("jt", 1e6), ("ribu", 1e3), ("rb", 1e3),
                   ("k", 1e3)):
        if s.endswith(suf) and s[:-len(suf)]:
            s, mult = s[:-len(suf)], m
            break
    if not re.fullmatch(r"[0-9.,]+", s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")          # 5.000.000,50
    elif s.count(".") >= 2 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")                            # 5.000.000
    elif s.count(",") >= 2 or re.search(r",\d{3}$", s):
        s = s.replace(",", "")                            # 5,000,000
    else:
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    return val * mult if val > 0 else None

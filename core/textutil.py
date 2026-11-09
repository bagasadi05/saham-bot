"""Utilitas teks untuk pesan Telegram."""
from __future__ import annotations

TELEGRAM_LIMIT = 4096


def _pre_open(s: str) -> bool:
    """True jika blok <pre> masih terbuka di akhir potongan teks."""
    return s.count("<pre>") > s.count("</pre>")


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Potong pesan panjang di batas baris sambil menjaga tag <pre> valid."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        in_pre = _pre_open(buf)
        close_tag = "</pre>" if in_pre else ""
        if len(line) > limit:  # baris ekstrem: potong paksa
            if buf:
                parts.append(buf + close_tag)
                buf = ""
            prefix = "<pre>" if in_pre else ""
            step = max(1, limit - len(prefix) - (len("</pre>") if in_pre else 0))
            for i in range(0, len(line), step):
                chunk = prefix + line[i:i + step]
                if _pre_open(chunk):
                    chunk += "</pre>"
                parts.append(chunk)
            continue
        reserve = len(close_tag)
        if len(buf) + len(line) + 1 + reserve > limit:
            if buf:
                parts.append(buf + close_tag)
                buf = ("<pre>" + line) if in_pre else line
            else:
                parts.append(line)
                buf = ""
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        if _pre_open(buf):
            buf += "</pre>"
        parts.append(buf)
    return parts

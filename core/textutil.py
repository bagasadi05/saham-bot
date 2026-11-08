"""Utilitas teks untuk pesan Telegram."""
from __future__ import annotations

TELEGRAM_LIMIT = 4096


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Potong pesan panjang di batas baris (tag HTML tidak terbelah)."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(line) > limit:  # baris ekstrem: potong paksa
            if buf:
                parts.append(buf)
                buf = ""
            for i in range(0, len(line), limit):
                parts.append(line[i:i + limit])
            continue
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts

"""Handler `/sektor` (P18): kekuatan relatif sektor dari indeks BEI.

Dependensi bot (`reply_long`, `cooldown_ok`) diterima lewat `deps` supaya
pemecahan file tetap kompatibel dengan monkeypatch di tes.
"""
from __future__ import annotations

import asyncio
import logging

from telegram.constants import ChatAction

from core import bandar, sektor as sektormod
from core.reports import esc

log = logging.getLogger("sahambuddy")


def _bar(rank: int, s: dict) -> str:
    return (f"{rank}. <b>{esc(s['name'])}</b> "
            f"<i>({esc(s['code'])})</i> {s['pct']:+.2f}%").replace(".", ",")


def _sektor_text(summary: dict, deps) -> str:
    if not summary.get("ok"):
        return "\n".join([
            "🏭 <b>KEKUATAN SEKTOR</b>",
            "",
            "❌ Data sektor BEI tidak bisa dibaca saat ini.",
            f"<i>{esc(summary.get('reason') or 'tidak diketahui')}</i>",
            "",
            "Coba lagi nanti, atau pakai <code>/bandar</code> untuk indeks "
            "utama. Tidak ada angka sektor yang saya karang.",
            "",
            deps._SINYAL_DISCLAIMER,
        ])
    secs = summary["sectors"]
    head = ["🏭 <b>KEKUATAN SEKTOR</b> — BEI resmi"]
    meta = []
    if summary.get("date"):
        meta.append(esc(summary["date"]))
    meta.append(f"{summary['n']} sektor terbaca")
    meta.append(f"{summary['n_positive']} naik / {summary['n_negative']} turun")
    head.append("<i>" + " · ".join(meta) + "</i>")
    head.append("")
    head += [_bar(i, s) for i, s in enumerate(secs, 1)]
    best, worst = summary["best"], summary["worst"]
    if best["pct"] > 0:
        head += ["", f"💪 Terkuat: <b>{esc(best['name'])}</b> "
                     f"{best['pct']:+.2f}%".replace(".", ",")]
    if worst["pct"] < 0:
        head += [f"🩸 Terlemah: <b>{esc(worst['name'])}</b> "
                 f"{worst['pct']:+.2f}%".replace(".", ",")]
    head += [
        "",
        "<i>Ini perubahan indeks sektor resmi BEI hari itu, bukan rekomendasi. "
        "Sektor kuat = arus dana masuk ke kelompok itu; keputusan emiten tetap "
        "lewat /cek KODE.</i>",
        "",
        "Rincian: <code>/bandar</code> · sinyal emiten: <code>/sinyal</code>",
        "",
        deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(head)


async def do(update, args: list[str], deps):
    """Tampilkan rotasi sektor BEI: /sektor."""
    if not await deps.cooldown_ok(update, "sektor"):
        return
    msg = update.effective_message
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        rows = await asyncio.to_thread(bandar.get_index_summary)
    except bandar.IDXTidakTersedia as e:
        log.warning("indeks BEI diblokir: %s", e)
        await deps.reply_long(msg, "\n".join([
            "🔒 <b>Data BEI (idx.co.id) sedang diblokir Cloudflare.</b>",
            "",
            "Rotasi sektor butuh indeks resmi BEI, jadi bagian ini tidak bisa "
            "diisi dari Yahoo. Coba lagi nanti atau dari server lain.",
            "",
            "Alternatif: <code>/sinyal beli bagus</code> (snapshot lokal).",
            "",
            deps._SINYAL_DISCLAIMER,
        ]))
        return
    except Exception:
        log.exception("gagal ambil indeks sektor")
        await deps.reply_long(msg, "\n".join([
            "❌ Data sektor BEI tidak tersedia saat ini.",
            "Coba lagi nanti — tidak ada angka yang saya karang.",
            "",
            deps._SINYAL_DISCLAIMER,
        ]))
        return
    await deps.reply_long(msg, _sektor_text(sektormod.summarize(rows), deps))

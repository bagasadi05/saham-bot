"""Ceklist sebelum beli (`/cek`): enam bagian ringkas + verdict faktual.

Modul ini **tidak mengambil data sendiri**. Semua angka datang dari fetcher
yang sudah dipakai perintah lain (`analysis.analyze`, `fundamental`, `news`,
`bandar`, `marketcal`, `banding.sector_context`), lalu di sini hanya diputuskan
apa yang ditampilkan dan bagaimana menyimpulkan — supaya bisa diuji offline.

Aturan yang dipakai (bisa dibaca, bukan selera):

  Tren        skor harian >= 20 → +2, >= 0 → +1, negatif → 0;
              tren mingguan naik → +1, turun → -1.
  Valuasi     laba negatif (PER < 0 / ROE < 0) → -2;
              PER > 40 atau PBV > 6 → -1 (mahal);
              PER <= 20 atau PBV <= 2 → +1 (murah); ROE >= 15% → +1.
  Berita      sentimen >= +20 → +1; <= -30 → -1.
  Asing       net asing beli → +1; net jual → -1 (kalau datanya ada).
  Likuiditas  >= Rp5 miliar/hari → +1; < Rp2 miliar/hari → veto.
  Agenda      ex-date <= 14 hari → catatan (bukan poin).

  Veto (langsung HINDARI): likuiditas < Rp2 miliar/hari, harga < Rp50,
  papan Pemantauan Khusus, laba negatif (ROE < 0).
  Poin >= 5 -> LAYAK; >= 2 -> TUNGGU; selain itu HINDARI. Bila >= 3 bagian
  tidak punya data, verdict-nya TIDAK CUKUP DATA — bukan HINDARI, karena
  menyimpulkan tanpa data sama saja mengarang.

Batas kejujuran: bagian tanpa data ditulis "tidak tersedia" beserta alasannya,
tidak diisi nol. Verdict adalah ringkasan aturan di atas — bukan probabilitas;
`/validasi` menunjukkan IC skor hanya 0,0115 dengan t_hac 0,85.
"""
from __future__ import annotations

import inspect
import math
import re

from core import fundamental, pricing

LIQ_GOOD = 5_000_000_000.0      # Rp5 miliar/hari: cukup untuk ritel tanpa slip
LIQ_MIN = 2_000_000_000.0       # di bawah ini = kurang likuid (sama dgn /paket)
MIN_PRICE = 50.0
AGENDA_WARN_DAYS = 14
PER_MAHAL = 40.0
PBV_MAHAL = 6.0
PER_MURAH = 20.0
PBV_MURAH = 2.0
ROE_BAGUS = 15.0
NEWS_BAGUS = 20.0
NEWS_BURUK = -30.0

_FOREIGN_NET_KEYS = ("ForeignNet", "ForeignNetValue", "NetForeign",
                     "NetForeignValue", "ForeignNetBuy")
_FOREIGN_BUY_KEYS = ("ForeignBuy", "ForeignBuyValue", "ForeignBid")
_FOREIGN_SELL_KEYS = ("ForeignSell", "ForeignSellValue", "ForeignOffer")


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) \
            else float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _first(row: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in row:
            v = _num(row.get(k))
            if v is not None:
                return v
    return None


def miliar(v: float | None) -> str:
    """Rp5,0 miliar / Rp750 juta — untuk teks bagian likuiditas."""
    if v is None:
        return "tidak tersedia"
    if abs(v) >= 1e12:
        return f"Rp{v / 1e12:.1f} triliun".replace(".", ",")
    if abs(v) >= 1e9:
        return f"Rp{v / 1e9:.1f} miliar".replace(".", ",")
    return f"Rp{v / 1e6:.0f} juta"


# ------------------------------------------------------------------ bagian
def liquidity(value_avg20: float | None) -> dict:
    """Klasifikasi likuiditas dari rata-rata nilai transaksi 20 hari."""
    v = _num(value_avg20)
    if v is None:
        return {"level": None, "value": None,
                "text": "nilai transaksi harian tidak tersedia"}
    if v >= LIQ_GOOD:
        level, text = "likuid", f"{miliar(v)}/hari"
    elif v >= LIQ_MIN:
        level, text = "sedang", f"{miliar(v)}/hari (di bawah Rp5 miliar)"
    else:
        level = "kurang"
        text = f"{miliar(v)}/hari — di bawah ambang Rp2 miliar"
    return {"level": level, "value": round(v), "text": text}


def foreign_net(row: dict | None) -> dict:
    """Net aliran asing dari baris IDX.

    Nama field berbeda antar-versi payload; kalau tidak ada yang dikenal,
    kembalikan `available=False` — jangan menebak tanda.
    """
    if not row:
        return {"available": False, "net": None, "text": "data BEI tidak tersedia"}
    net = _first(row, _FOREIGN_NET_KEYS)
    if net is None:
        buy = _first(row, _FOREIGN_BUY_KEYS)
        sell = _first(row, _FOREIGN_SELL_KEYS)
        if buy is not None and sell is not None:
            net = buy - sell
    if net is None:
        return {"available": False, "net": None,
                "text": "field aliran asing tidak ada di payload BEI"}
    arah = "beli bersih" if net > 0 else ("jual bersih" if net < 0 else "netral")
    return {"available": True, "net": round(net),
            "text": f"{miliar(abs(net))} {arah}"}


def agenda_note(agenda: dict | None, today_days: int | None = None) -> dict:
    """Catatan ex-date dividen <= 14 hari.

    `today_days` = sisa hari menuju ex-date (dihitung pemanggil, karena modul
    ini tidak memegang jam). None = tidak diketahui.
    """
    if not agenda:
        return {"near": False, "text": "tidak ada ex-date dividen terdekat"}
    days = today_days
    amount = _num(agenda.get("amount"))
    if days is not None and days > AGENDA_WARN_DAYS:
        return {"near": False, "text": f"ex-date {days} hari lagi"}
    amt = f" Rp{amount:,.0f}/lembar".replace(",", ".") if amount else ""
    when = f"{days} hari lagi" if days is not None else "tanggal tidak jelas"
    return {"near": True,
            "text": f"ex-dividen {when}{amt} — harga biasanya turun "
                    "sebesar dividen"}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def _collect_data_and_arb_warnings(
    res: dict,
    snap: dict,
    price: float | None,
    warnings: list[str],
    facts: list[tuple[int, str]],
) -> None:
    """Tambahkan peringatan eksplisit kualitas data, suspensi, dan risiko gap ARB."""

    def _add_warn(msg: str, weight: int = 0) -> None:
        clean = _strip_html(msg)
        if clean and clean not in warnings:
            warnings.append(clean)
            if weight < 0:
                facts.append((weight, clean))

    quality_note = _strip_html(res.get("quality_note") or "")
    if quality_note:
        _add_warn(quality_note, -2)

    stale_note = _strip_html(res.get("stale_note") or "")
    if stale_note and stale_note not in quality_note:
        _add_warn(stale_note, -1)

    quality = res.get("quality") if isinstance(res.get("quality"), dict) else {}
    flags = quality.get("flags") if isinstance(quality.get("flags"), dict) else {}
    if flags.get("volume_nol"):
        _add_warn(
            f"Peringatan suspensi/likuiditas: {flags['volume_nol']} bar volume 0 terdeteksi",
            -2,
        )
    if flags.get("stale_days"):
        _add_warn(
            f"Data harga tertinggal {flags['stale_days']} hari bursa (potensi suspensi/cache lama)",
            -2,
        )

    if res.get("suspended") or snap.get("suspended") or snap.get("halt"):
        _add_warn("Peringatan suspensi perdagangan / penghentian sementara aktif", -3)

    vol = _num(snap.get("volume"))
    if vol is not None and vol == 0:
        _add_warn(
            "Volume hari terakhir 0 — waspada suspensi atau antrean terkunci (ARB/ARA)",
            -2,
        )

    ret1d = _num(snap.get("ret1d"))
    if res.get("arb_locked") or snap.get("arb_locked") or (ret1d is not None and ret1d <= -14.0):
        _add_warn(
            "Risiko gap ARB: tekanan turun tajam/terkunci membuat eksekusi jual sulit",
            -2,
        )

    explicit_arb = res.get("arb_gap_risk") or res.get("arb_warning")
    if explicit_arb:
        if isinstance(explicit_arb, dict):
            msg = explicit_arb.get("warning") or explicit_arb.get("message") or explicit_arb.get("reason")
            if explicit_arb.get("risk") or explicit_arb.get("high_risk") or msg:
                _add_warn(str(msg or "Risiko gap ARB terdeteksi"), -2)
        elif isinstance(explicit_arb, str):
            _add_warn(explicit_arb, -2)
        elif explicit_arb is True:
            _add_warn("Risiko gap ARB terdeteksi (potensi melewati stop loss)", -2)

    arb_fn = getattr(pricing, "check_arb_gap_risk", None)
    if callable(arb_fn):
        try:
            sig = inspect.signature(arb_fn)
            ind = res.get("ind")
            sl = _num(res.get("sl") or snap.get("sl"))
            prev_close = _num(res.get("prev_close") or snap.get("prev_close"))
            day = res.get("day") or snap.get("as_of")
            pool = {
                "df": ind,
                "ind": ind,
                "price": price,
                "entry": price,
                "entry_price": price,
                "close": price,
                "sl": sl,
                "stop_loss": sl,
                "prev_close": prev_close,
                "day": day,
                "res": res,
            }
            pos_args: list = []
            kw: dict = {}
            has_varkw = False
            for pname, pinfo in sig.parameters.items():
                if pinfo.kind == inspect.Parameter.VAR_KEYWORD:
                    has_varkw = True
                    continue
                val = pool.get(pname)
                if pinfo.kind == inspect.Parameter.POSITIONAL_ONLY:
                    if val is None and pinfo.default is inspect.Parameter.empty:
                        val = ind if ind is not None else price
                    if val is not None:
                        pos_args.append(val)
                    elif pinfo.default is inspect.Parameter.empty:
                        pos_args.append(None)
                elif val is not None:
                    kw[pname] = val
            if has_varkw:
                for k in ("price", "sl", "prev_close", "ind", "res"):
                    if pool[k] is not None and k not in kw:
                        kw[k] = pool[k]
            missing_req = [
                pname
                for pname, pinfo in sig.parameters.items()
                if pinfo.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                and pinfo.default is inspect.Parameter.empty
                and pname not in kw
            ]
            if not missing_req:
                arb_res = arb_fn(*pos_args, **kw) if (pos_args or kw or not sig.parameters) else None
            elif len(missing_req) == 1 and (ind is not None or price is not None):
                kw[missing_req[0]] = ind if ind is not None else price
                arb_res = arb_fn(*pos_args, **kw)
            else:
                arb_res = None
            if isinstance(arb_res, dict):
                if arb_res.get("risk") or arb_res.get("has_risk") or arb_res.get("gap_risk"):
                    _add_warn(
                        str(
                            arb_res.get("warning")
                            or arb_res.get("message")
                            or arb_res.get("reason")
                            or "Risiko gap ARB terdeteksi oleh aturan tick/ARB"
                        ),
                        -2,
                    )
            elif isinstance(arb_res, str) and arb_res.strip():
                _add_warn(arb_res.strip(), -2)
            elif arb_res is True:
                _add_warn("Risiko gap ARB terdeteksi oleh aturan tick/ARB", -2)
        except Exception:
            pass


# ------------------------------------------------------------------ verdict
def build(*, res: dict | None, fund: dict | None, fund_raw: dict | None,
          nsent: dict | None, bandar_row: dict | None,
          agenda: dict | None, agenda_days: int | None,
          peers: dict | None, value_avg20: float | None,
          board: str | None = None) -> dict:
    """Susun enam bagian ceklist + verdict. Semua argumen boleh None."""
    res = res or {}
    fund_raw = fund_raw or {}
    snap = res.get("snap") or {}
    price = _num(snap.get("close"))
    tech = _num((res.get("score") or {}).get("pct"))
    weekly = res.get("weekly") or {}
    wdir = weekly.get("direction")

    points = 0
    facts: list[tuple[int, str]] = []
    items: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []

    # 1. Tren ---------------------------------------------------------------
    if tech is None and wdir is None:
        status, head = "na", "data tren tidak tersedia"
        missing.append("tren")
        detail = ""
    else:
        if tech is not None:
            points += 2 if tech >= 20 else (1 if tech >= 0 else 0)
        if wdir == "naik":
            points += 1
        elif wdir == "turun":
            points -= 1
        selaras = (tech is not None and tech >= 0 and wdir == "naik")
        lawan = (tech is not None and tech < 0 and wdir == "turun")
        status = "ok" if selaras else ("bad" if lawan else "warn")
        head = {"ok": "selaras naik", "warn": "campur / belum konfirmasi",
                "bad": "turun"}[status]
        bits = []
        if tech is not None:
            bits.append(f"skor harian {tech:+.0f} (skala -100..100)")
        if wdir:
            pct = _num(weekly.get("pct_vs_ma"))
            bits.append(f"mingguan {wdir}" + (f" {pct:+.1f}% vs MA20"
                                              .replace(".", ",")
                                              if pct is not None else ""))
        else:
            bits.append("tren mingguan tidak tersedia")
        detail = " · ".join(bits)
        if weekly.get("warning"):
            detail += f" · catatan: {weekly['warning']}"
        if status == "ok":
            facts.append((3, "Tren harian dan mingguan searah naik."))
        elif status == "bad":
            facts.append((-3, "Tren harian dan mingguan sama-sama turun."))
    items.append({"key": "tren", "label": "Tren", "status": status,
                  "headline": head, "detail": detail})

    # 2. Kinerja harga 20h vs peers & ambang valuasi absolut ----------------
    per = _num(fund_raw.get("per"))
    pbv = _num(fund_raw.get("pbv"))
    roe = fundamental.normalize_roe(
        _num(fund_raw.get("roe")),
        unit=fund_raw.get("roe_unit"),
    )
    sector = fund_raw.get("sector") or (peers or {}).get("sector")
    laba_negatif = (per is not None and per < 0) or (roe is not None and roe < 0)
    mahal = ((per is not None and per > PER_MAHAL)
             or (pbv is not None and pbv > PBV_MAHAL))
    per_murah = per is not None and 0 < per <= PER_MURAH
    pbv_murah = pbv is not None and 0 < pbv <= PBV_MURAH
    both_present = per is not None and pbv is not None
    both_murah = per_murah and pbv_murah
    one_murah = per_murah or pbv_murah
    if per is None and pbv is None and roe is None:
        v_status, v_head = "na", "valuasi tidak tersedia"
        missing.append("valuasi")
        v_detail = ""
    else:
        roe_tipis = roe is not None and 0 <= roe < 2
        if laba_negatif:
            points -= 2
            v_status, v_head = "bad", "laba negatif"
        elif roe_tipis:
            # PER/PBV "murah" tidak berarti apa-apa kalau labanya nyaris nol.
            v_status, v_head = "warn", "laba nyaris nol"
            points -= 1
        elif mahal:
            points -= 1
            v_status, v_head = "warn", "Mahal"
        elif both_murah:
            points += 1
            v_status, v_head = "ok", "Murah (PER & PBV)"
        elif both_present and one_murah:
            v_status, v_head = "warn", "Campuran (salah satu murah, satunya tinggi)"
        elif not both_present and one_murah:
            points += 1
            v_status, v_head = "ok", "Murah (parsial; rasio pasangan tidak tersedia)"
        else:
            v_status, v_head = "warn", "wajar, bukan murah"
        if roe is not None and roe >= ROE_BAGUS:
            points += 1
        vals = []
        if per is not None:
            vals.append(f"PER {per:.1f}".replace(".", ","))
        if pbv is not None:
            vals.append(f"PBV {pbv:.2f}".replace(".", ","))
        if roe is not None:
            vals.append(f"ROE {roe:.1f}%".replace(".", ","))
        v_detail = " · ".join(vals)
        if per is not None or pbv is not None:
            v_detail += " (ambang bot absolut PER≤20, PBV≤2)"
        if roe is None:
            v_detail += " · ROE tidak tersedia"
            warnings.append("ROE tidak tersedia — valuasi hanya dari PER/PBV")
        if sector:
            v_detail += f" · sektor {sector}"
        sec_per = _num((peers or {}).get("per_median") or (peers or {}).get("sector_per"))
        sec_pbv = _num((peers or {}).get("pbv_median") or (peers or {}).get("sector_pbv"))
        if sec_per is not None or sec_pbv is not None:
            sec_bits = []
            if sec_per is not None:
                sec_bits.append(f"median PER sektor {sec_per:.1f}".replace(".", ","))
            if sec_pbv is not None:
                sec_bits.append(f"median PBV sektor {sec_pbv:.2f}".replace(".", ","))
            v_detail += " · " + ", ".join(sec_bits)
        peer_ret = _num((peers or {}).get("ret20d"))
        if peer_ret is not None:
            sec_tag = "" if (sec_per is not None or sec_pbv is not None) else "; median PER/PBV sektor tidak tersedia"
            v_detail += (
                f" · kinerja harga 20h peers {peer_ret:+.1f}%{sec_tag}"
                .replace(".", ",")
            )
        if laba_negatif:
            facts.append((-4, "Perusahaan sedang merugi (PER/ROE negatif)."))
        elif roe_tipis:
            facts.append((-2, (f"ROE {roe:.1f}% — laba nyaris nol, valuasi "
                               "murah tidak otomatis aman.").replace(".", ",")))
        elif mahal:
            facts.append((-2, f"Valuasi mahal menurut ambang absolut (PER {per}, PBV {pbv})."
                          if per is not None and pbv is not None
                          else "Valuasi di atas ambang absolut bot."))
        elif both_murah:
            facts.append((2, "Valuasi murah menurut ambang absolut bot (PER & PBV)."))
        elif both_present and one_murah:
            facts.append((-1, (
                f"Valuasi campuran (PER {per:.1f}, PBV {pbv:.2f} — "
                "salah satu murah, satunya tinggi)."
            ).replace(".", ",")))
        elif not both_present and one_murah:
            facts.append((1, "Valuasi di bawah ambang absolut bot pada metrik yang tersedia."))
        if roe is not None and roe >= ROE_BAGUS:
            facts.append((2, f"ROE {roe:.1f}% — profitabilitas kuat."
                          .replace(".", ",")))
        elif roe is not None and 0 <= roe < 5:
            facts.append((-1, f"ROE tipis ({roe:.1f}%)."))
    items.append({"key": "valuasi", "label": "Kinerja harga 20h vs peers",
                  "status": v_status, "headline": v_head, "detail": v_detail})

    # 3. Aliran asing -------------------------------------------------------
    fg = foreign_net(bandar_row)
    if not fg["available"]:
        a_status, a_head, a_detail = "na", "tidak tersedia", fg["text"]
        missing.append("aliran asing")
    else:
        net = fg["net"] or 0
        if net > 0:
            points += 1
            a_status, a_head = "ok", "asing beli bersih"
            facts.append((1, "Asing masih membeli bersih hari itu."))
        elif net < 0:
            points -= 1
            a_status, a_head = "warn", "asing jual bersih"
            facts.append((-1, "Asing menjual bersih hari itu."))
        else:
            a_status, a_head = "warn", "netral"
        a_detail = fg["text"]
    items.append({"key": "asing", "label": "Aliran asing", "status": a_status,
                  "headline": a_head, "detail": a_detail})

    # 4. Berita -------------------------------------------------------------
    n_pct = _num((nsent or {}).get("pct"))
    n_count = (nsent or {}).get("n")
    if n_pct is None:
        b_status, b_head = "na", "sentimen tidak tersedia"
        missing.append("berita")
        b_detail = ""
    else:
        if n_pct >= NEWS_BAGUS:
            points += 1
            b_status, b_head = "ok", "positif"
            facts.append((2, f"Sentimen berita positif ({n_pct:+.0f})."))
        elif n_pct <= NEWS_BURUK:
            points -= 1
            b_status, b_head = "bad", "negatif"
            facts.append((-2, f"Sentimen berita negatif ({n_pct:+.0f})."))
        else:
            b_status, b_head = "warn", "netral"
        b_detail = f"{n_count or 0} berita, sentimen {n_pct:+.0f}"
        top = (nsent or {}).get("top")
        if top:
            b_detail += f" · “{top}”"
    items.append({"key": "berita", "label": "Berita", "status": b_status,
                  "headline": b_head, "detail": b_detail})

    # 5. Agenda -------------------------------------------------------------
    ag = agenda_note(agenda, agenda_days)
    g_status = "warn" if ag["near"] else "ok"
    items.append({"key": "agenda", "label": f"Agenda {AGENDA_WARN_DAYS} hari",
                  "status": g_status,
                  "headline": "ada agenda" if ag["near"] else "tidak ada",
                  "detail": ag["text"]})
    if ag["near"] and agenda_days is not None:
        facts.append((-1, f"Ex-dividen {agenda_days} hari lagi — harga "
                          "biasanya turun sebesar dividen."))

    # 6. Likuiditas ---------------------------------------------------------
    liq = liquidity(value_avg20)
    if liq["level"] is None:
        l_status, l_head = "na", "tidak terukur"
        missing.append("likuiditas")
    else:
        l_status = {"likuid": "ok", "sedang": "warn", "kurang": "bad"}[liq["level"]]
        l_head = liq["level"]
        if liq["level"] == "likuid":
            points += 1
            facts.append((1, f"Likuiditas {liq['text']} — cukup untuk ritel."))
        elif liq["level"] == "kurang":
            facts.append((-4, f"Likuiditas {liq['text']}."))
    items.append({"key": "likuiditas", "label": "Likuiditas", "status": l_status,
                  "headline": l_head, "detail": liq["text"]})

    # 6b. Audit forensik arus kas & jebakan dividen (offline, jujur bila NA)
    cf = fundamental.analyze_cashflow_quality(fund_raw)
    for flg in cf["flags"]:
        warnings.append(flg)
        facts.append((-2, flg))
    for ok_note in cf["strengths"]:
        facts.append((1, ok_note))

    # 6c. Kualitas data OHLCV, risiko suspensi & gap ARB
    _collect_data_and_arb_warnings(res, snap, price, warnings, facts)

    # Veto & verdict --------------------------------------------------------
    vetoes: list[str] = []
    if liq["level"] == "kurang":
        vetoes.append("likuiditas < Rp2 miliar/hari")
    if price is not None and price < MIN_PRICE:
        vetoes.append("harga < Rp50")
    if (board or "").lower().startswith("pemantauan"):
        vetoes.append("papan Pemantauan Khusus")
    if laba_negatif:
        vetoes.append("laba negatif")
    elif roe is not None and 0 <= roe < 2:
        vetoes.append("ROE < 2% (laba nyaris nol)")

    if vetoes:
        verdict = "HINDARI"
    elif sum(1 for it in items if it["status"] == "na") >= 3:
        verdict = "TIDAK CUKUP DATA"
    elif points >= 5:
        verdict = "LAYAK"
    elif points >= 2:
        verdict = "TUNGGU"
    else:
        verdict = "HINDARI"

    if verdict == "TIDAK CUKUP DATA":
        reasons = ["Terlalu banyak bagian tanpa data — jangan dipaksa."]
    else:
        ranked = (sorted([f for f in facts if f[0] < 0], key=lambda f: f[0])
                  if verdict == "HINDARI"
                  else sorted(facts, key=lambda f: -f[0]))
        reasons = [t for _, t in ranked[:2]]
    if vetoes:
        reasons = [f"Veto: {v}." for v in vetoes[:2]]

    bull_points = [t for _, t in sorted(
        [f for f in facts if f[0] > 0], key=lambda x: -x[0],
    )[:3]]
    bear_points: list[str] = [f"Veto: {v}." for v in vetoes]
    for _, t in sorted([f for f in facts if f[0] < 0], key=lambda x: x[0]):
        if t not in bear_points:
            bear_points.append(t)
    bear_points = bear_points[:3]

    return {
        "ticker": res.get("ticker") or (fund_raw.get("symbol") or ""),
        "price": price,
        "board": board,
        "sector": sector,
        "items": items,
        "points": points,
        "vetoes": vetoes,
        "verdict": verdict,
        "reasons": reasons,
        "bull_points": bull_points,
        "bear_points": bear_points,
        "cashflow": cf,
        "missing": missing,
        "warnings": warnings,
    }

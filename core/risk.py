"""Metrik risiko portofolio asli (P23/P24) — tanpa janji untung.

Semua angka dihitung dari posisi + riwayat harga harian yang diberikan
pemanggil. Tidak ada sumber data baru dan tidak ada angka yang dikarang:
bagian yang riwayatnya tidak tersedia ditandai `ok=False` / `None`.

Asumsi yang ditulis terbuka:
  - Return portofolio memakai **bobot sekarang** dan dianggap di-rebalance
    harian (bukan buy-and-hold). Ini menjawab "risiko kalau komposisi hari ini
    dipegang", bukan return realisasi.
  - Drawdown dihitung dari kurva ekuitas sintetis dengan bobot itu.
  - VaR 95% = 1,645 x vol harian x nilai portofolio (parametrik, bukan janji).
  - VaR historis 95% = persentil 5% return portofolio harian; ES 95% =
    rata-rata return di ekor itu. Keduanya dilaporkan sebagai **besar
    kerugian** (positif), jadi ES >= VaR selalu berlaku.
  - Kontribusi risiko RC_i = w_i (Σw)_i / σ_p² x 100, memakai matriks
    kovarians dari tanggal yang sama-sama tersedia.
  - Bobot inv-vol dibatasi dua lapis: per posisi & per sektor; bila batas itu
    hanya memungkinkan sebagian modal teralokasi, sisanya dilaporkan sebagai
    tunai, bukan bobot karangan.
  - Korelasi/beta memakai irisan tanggal yang tersedia di semua seri.
"""
from __future__ import annotations

import math

import pandas as pd

MAX_POS_PCT = 0.25        # satu posisi maks 25% dari modal acuan
MAX_SECTOR_PCT = 0.40     # satu sektor maks 40%
MIN_POS_LIMIT = 0.05      # batas minimum posisi kustom (5%)
MAX_POS_LIMIT = 0.50      # batas maksimum posisi kustom (50%)
MIN_SEC_LIMIT = 0.10      # batas minimum sektor kustom (10%)
MAX_SEC_LIMIT = 0.80      # batas maksimum sektor kustom (80%)
CORR_WARN = 0.80          # korelasi pasangan tertinggi
BETA_WARN = 1.30
DD_WARN_PCT = -15.0       # drawdown berjalan
MIN_DAYS = 20
UNKNOWN_SECTOR = "tidak diketahui"
MAX_WF_ROUNDS = 50        # putaran maksimum water-filling (posisi & sektor)
VAR_LEVEL = 0.05          # persentil 5% => VaR 95%


def validate_limits(pos_pct: float, sec_pct: float) -> tuple[float, float]:
    """Validasi batas posisi & sektor kustom (fraksi 0.05..0.50 & 0.10..0.80)."""
    p = float(pos_pct)
    s = float(sec_pct)
    if not (math.isfinite(p) and math.isfinite(s)):
        raise ValueError("Batas risiko harus berupa angka valid.")
    if p < MIN_POS_LIMIT or p > MAX_POS_LIMIT:
        raise ValueError("Batas posisi harus antara 5% dan 50% modal.")
    if s < MIN_SEC_LIMIT or s > MAX_SEC_LIMIT:
        raise ValueError("Batas sektor harus antara 10% dan 80% modal.")
    if s < p:
        raise ValueError("Batas sektor tidak boleh lebih kecil dari batas posisi.")
    return round(p, 4), round(s, 4)


def get_risk_limits(
    chat_id: int | str | None,
    default_pos: float = MAX_POS_PCT,
    default_sec: float = MAX_SECTOR_PCT,
) -> tuple[float, float]:
    """Ambil batas posisi & sektor per chat (delegasi ke store agar satu sumber)."""
    if chat_id is None:
        return float(default_pos), float(default_sec)
    from core import store

    return store.get_risk_limits(chat_id, default_pos=default_pos, default_sec=default_sec)


def _sector_map(sectors: dict | None) -> dict[str, str]:
    """Normalisasi peta ticker → sektor; entri kosong dianggap tidak diketahui."""
    out: dict[str, str] = {}
    for raw_t, raw_s in (sectors or {}).items():
        t = str(raw_t or "").strip().upper()
        s = str(raw_s or "").strip()
        if t and s:
            out[t] = s
    return out


def _water_fill(
    base: dict[str, float],
    caps: dict[str, float],
    budget: float = 1.0,
    max_rounds: int = MAX_WF_ROUNDS,
) -> tuple[dict[str, float], bool]:
    """Bagi `budget` proporsional `base` dengan batas atas per kunci.

    Water-filling: kunci yang melewati batasnya dipatok di batas, sisa anggaran
    dibagi ulang proporsional ke kunci yang masih bebas — diulang maksimum
    `max_rounds` putaran, urutan abjad supaya deterministik. Bila jumlah semua
    batas < `budget`, tidak ada angka karangan: selisihnya tinggal sebagai sisa
    yang tidak teralokasi dan dilaporkan pemanggil sebagai tunai.

    Return `(alokasi, ada_batas_mengikat)`.
    """
    alloc: dict[str, float] = {}
    free = {str(k): float(v) for k, v in base.items() if float(v) > 0}
    bound = False
    for _ in range(max(1, int(max_rounds))):
        if not free:
            break
        rem = float(budget) - sum(alloc.values())
        if rem <= 1e-12:
            break
        free_sum = sum(free.values())
        if free_sum <= 0:
            break
        scaled = {k: v / free_sum * rem for k, v in free.items()}
        over = [k for k in sorted(scaled)
                if scaled[k] > float(caps.get(k, 0.0)) + 1e-12]
        if not over:
            alloc.update(scaled)
            break
        bound = True
        for k in over:
            alloc[k] = float(caps.get(k, 0.0))
            free.pop(k, None)
    return alloc, bound


def inverse_vol_weights(
    quotes: dict[str, dict],
    tickers: list[str],
    *,
    max_weight: float = MAX_POS_PCT,
    max_sector_pct: float = MAX_SECTOR_PCT,
    sectors: dict[str, str] | None = None,
) -> dict:
    """Target bobot penyeimbang risiko (1 / volatilitas) ala Riskfolio-Lib.

    Hanya menghitung emiten yang punya riwayat return harian >= MIN_DAYS dan
    volatilitas positif; bila < 2 emiten memenuhi syarat, kembalikan ok=False
    tanpa mengarang volatilitas.

    Dua lapis batas: (1) bobot per posisi <= `max_weight`; (2) total bobot per
    sektor <= `max_sector_pct` bila peta `sectors` (ticker → sektor) diberikan.
    Kelebihan satu sektor dialirkan ke posisi/sektor lain yang masih di bawah
    batas lewat water-filling iteratif (maks 50 putaran, deterministik).

    Emiten tanpa sektor masuk kelompok "tidak diketahui" dan **tidak** dipaksa
    ke batas sektor mana pun (sektornya memang tidak diketahui) — hanya batas
    per posisi yang berlaku. Bila batas-batas itu hanya memungkinkan sebagian
    modal teralokasi, sisa dilaporkan di `cash_pct`/`cap_note`, bukan dikarang
    supaya tetap 100%.
    """
    vols: dict[str, float] = {}
    for raw_t in tickers:
        t = str(raw_t or "").upper()
        if not t or t in vols:
            continue
        rets = (quotes.get(t) or {}).get("rets")
        if not isinstance(rets, pd.Series) or len(rets) < MIN_DAYS:
            continue
        vol = float(rets.std(ddof=1))
        if math.isfinite(vol) and vol > 0:
            vols[t] = vol

    if len(vols) < 2:
        return {
            "ok": False,
            "weights": {},
            "vols_ann": {},
            "capped": False,
            "cap_pct": round(float(max_weight or MAX_POS_PCT) * 100.0, 1),
            "cap_note": None,
            "sector_capped": False,
            "sector_note": None,
            "sectors_pct": {},
            "cash_pct": 0.0,
            "reason": "butuh minimal 2 posisi dengan riwayat volatilitas cukup",
        }

    inv = {t: 1.0 / v for t, v in vols.items()}
    total_inv = sum(inv.values())
    base = {t: val / total_inv for t, val in inv.items()}

    cap = float(max_weight or MAX_POS_PCT)
    if not math.isfinite(cap) or cap <= 0:
        cap = MAX_POS_PCT
    cap = min(cap, 1.0)

    sec_cap: float | None = None
    if sectors:
        raw_sec = float(
            max_sector_pct if max_sector_pct is not None else MAX_SECTOR_PCT)
        if math.isfinite(raw_sec) and 0 < raw_sec < 1.0:
            sec_cap = raw_sec
    secmap = _sector_map(sectors)

    # Kelompok tingkat-2: satu sektor = satu grup; emiten tanpa sektor menjadi
    # grup sendiri (hanya batas per posisi, tidak dipaksa batas sektor).
    groups: dict[str, list[str]] = {}
    group_sector: dict[str, str | None] = {}
    for t in base:
        s = secmap.get(t) if sec_cap is not None else None
        key = s if s else f"~{t}"
        groups.setdefault(key, []).append(t)
        group_sector[key] = s

    g_base = {g: sum(base[t] for t in members) for g, members in groups.items()}
    g_caps = {
        g: min(cap * len(members), sec_cap) if group_sector[g] else cap
        for g, members in groups.items()
    }
    g_alloc, _ = _water_fill(g_base, g_caps, 1.0)

    weights: dict[str, float] = {}
    for g, members in groups.items():
        amount = g_alloc.get(g, 0.0)
        if amount <= 0:
            continue
        alloc, _ = _water_fill(
            {t: base[t] for t in members}, {t: cap for t in members}, amount)
        weights.update(alloc)

    # Posisi dianggap kena batas bila bobotnya berhenti tepat di batas padahal
    # proporsi awalnya lebih besar (bisa dipatok di tingkat grup maupun posisi).
    pos_capped = any(
        weights[t] >= cap - 1e-9 and base[t] > weights[t] + 1e-9 for t in base)

    invested = sum(weights.values())
    cash = max(0.0, 1.0 - invested)

    sectors_tot: dict[str, float] = {}
    for t, val in weights.items():
        s = (secmap.get(t) if sec_cap is not None else None) or UNKNOWN_SECTOR
        sectors_tot[s] = sectors_tot.get(s, 0.0) + val

    capped_sectors: list[tuple[str, float, float]] = []
    for g in groups:
        s = group_sector[g]
        if not s or sec_cap is None:
            continue
        if abs(g_caps[g] - sec_cap) > 1e-9:
            continue   # kapasitas grup dibatasi batas posisi, bukan sektor
        alloc_g = g_alloc.get(g, 0.0)
        if alloc_g >= g_caps[g] - 1e-9 and g_base[g] > alloc_g + 1e-9:
            capped_sectors.append((s, g_base[g], alloc_g))
    capped_sectors.sort(key=lambda row: (-row[1], row[0]))
    sector_capped = bool(capped_sectors)

    notes: list[str] = []
    if capped_sectors:
        detail = ", ".join(
            f"{s} {a * 100:.1f}% (proporsi awal {b * 100:.1f}%)"
            .replace(".", ",")
            for s, b, a in capped_sectors)
        notes.append(f"batas sektor {sec_cap * 100:.0f}% mengikat: {detail}")
    known = [t for t in base if secmap.get(t)]
    if sec_cap is not None and len(known) < len(base):
        notes.append(
            f"{len(base) - len(known)} emiten tanpa sektor — dihitung "
            f"'{UNKNOWN_SECTOR}', tidak dipaksa ke batas sektor")
    if sec_cap is None:
        if sectors:
            notes.append("batas sektor tidak diterapkan (batas 100%)")
        else:
            notes.append(
                "peta sektor tidak diberikan — batas sektor tidak diterapkan")
    sector_note = "; ".join(notes) if notes else None

    cap_note = None
    if cash > 0.005:
        batas = f"batas {cap * 100:.0f}%/posisi"
        if sec_cap is not None:
            batas += f" + {sec_cap * 100:.0f}%/sektor"
        cap_note = (
            f"{batas} hanya memungkinkan {invested * 100:.1f}% modal "
            f"teralokasi; sisa {cash * 100:.1f}% tunai").replace(".", ",")

    return {
        "ok": True,
        "weights": {t: round(val * 100.0, 1) for t, val in weights.items()},
        "vols_ann": {t: round(v * math.sqrt(252) * 100.0, 1) for t, v in vols.items()},
        "capped": pos_capped,
        "cap_pct": round(cap * 100.0, 1),
        "cap_note": cap_note,
        "sector_capped": sector_capped,
        "sector_note": sector_note,
        "sectors_pct": {s: round(val * 100.0, 1) for s, val in sectors_tot.items()},
        "cash_pct": round(cash * 100.0, 1),
    }


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def portfolio_risk(positions: list[dict], quotes: dict[str, dict],
                   bench_rets: pd.Series | None = None,
                   *,
                   max_pos_pct: float = MAX_POS_PCT,
                   max_sector_pct: float = MAX_SECTOR_PCT) -> dict:
    """Hitung eksposur, konsentrasi, drawdown, korelasi, beta, VaR.

    `quotes[ticker]` harus memuat `close` dan (opsional) `rets` = Series
    return harian. Posisi tanpa harga/riwayat dikeluarkan dari hitungan dan
    dilaporkan jumlahnya.
    """
    priced = []
    for p in positions:
        t = (p.get("ticker") or "").upper()
        q = quotes.get(t) or {}
        close = q.get("close")
        lots = int(p.get("lots") or 0)
        if not close or lots <= 0:
            continue
        value = float(close) * lots * 100
        rets = q.get("rets")
        priced.append({
            "ticker": t, "value": value, "lots": lots,
            "sector": p.get("sector") or "tidak diketahui",
            "rets": rets if isinstance(rets, pd.Series) else None,
        })

    if not priced:
        return {"ok": False, "reason": "tidak ada posisi dengan harga terbaca",
                "value": 0, "weights": {}, "sectors": [], "warnings": [],
                "risk_contrib_pct": None, "risk_contrib_note": None,
                "hist_var95_pct": None, "hist_var95_rp": None,
                "hist_es95_pct": None, "hist_es95_rp": None,
                "var_n_days": None, "var_breaches": None,
                "var_expected_breaches": None, "var_note": None}

    total = sum(x["value"] for x in priced)
    weights = {x["ticker"]: x["value"] / total for x in priced}

    sectors: dict[str, float] = {}
    for x in priced:
        sectors[x["sector"]] = sectors.get(x["sector"], 0.0) + x["value"]
    sector_rows = sorted(
        ({"sector": s, "value": round(v), "pct": _pct(v, total)}
         for s, v in sectors.items()), key=lambda r: -r["value"])

    rets = [x for x in priced if x["rets"] is not None and len(x["rets"]) >= MIN_DAYS]
    warnings: list[str] = []
    mdd = vol_ann = var95_pct = var95_rp = None
    beta = corr_avg = corr_max = None
    corr_pair = None
    risk_contrib: dict[str, float] | None = None
    rc_note: str | None = None
    hist_var_pct = hist_var_rp = hist_es_pct = hist_es_rp = None
    var_n_days = var_breaches = var_expected = None
    var_note: str | None = None
    days = 0

    if len(rets) < len(priced):
        warnings.append(
            f"{len(priced) - len(rets)} posisi tanpa riwayat cukup — "
            "tidak dihitung di drawdown/korelasi/kontribusi risiko/"
            "VaR historis")

    if rets:
        frame = pd.concat({x["ticker"]: x["rets"] for x in rets}, axis=1,
                          join="inner").dropna()
        days = len(frame)
        if days >= MIN_DAYS:
            w = pd.Series({x["ticker"]: weights[x["ticker"]] for x in rets})
            port = (frame * w).sum(axis=1)
            vol_daily = float(port.std(ddof=1))
            vol_ann = round(vol_daily * math.sqrt(252) * 100, 1)
            var95_pct = round(1.645 * vol_daily * 100, 2)
            var95_rp = round(total * 1.645 * vol_daily)
            equity = (1 + port).cumprod()
            mdd = round(float((equity / equity.cummax() - 1).min()) * 100, 1)

            # Kontribusi risiko: RC_i = w_i (Σw)_i / σ_p² x 100 (jumlah ~100).
            try:
                cov = frame.cov().to_numpy(dtype=float)
                wv = w.reindex(frame.columns).to_numpy(dtype=float)
                var_p = float(wv @ cov @ wv)
            except (TypeError, ValueError):
                var_p = float("nan")
            if math.isfinite(var_p) and var_p > 0:
                rc = wv * (cov @ wv) / var_p * 100.0
                risk_contrib = {str(t): round(float(val), 1)
                                for t, val in zip(frame.columns, rc, strict=False)}
            else:
                rc_note = "varians portofolio nol/tidak terhingga"

            # VaR historis 95% = persentil 5% return harian; ES = rata-rata ekor.
            q05 = float(port.quantile(VAR_LEVEL))
            hist_var_pct = round(max(0.0, -q05) * 100.0, 2)
            hist_var_rp = round(total * hist_var_pct / 100.0)
            tail = port[port <= q05 + 1e-12]
            var_n_days = int(len(port))
            var_breaches = int(len(tail))
            var_expected = round(var_n_days * VAR_LEVEL, 1)
            if len(tail):
                hist_es_pct = round(max(0.0, -float(tail.mean())) * 100.0, 2)
                hist_es_rp = round(total * hist_es_pct / 100.0)

            if len(rets) >= 2:
                corr = frame.corr()
                pairs = [(corr.iloc[i, j], frame.columns[i], frame.columns[j])
                         for i in range(len(corr)) for j in range(i + 1, len(corr))]
                if pairs:
                    corr_avg = round(float(sum(p[0] for p in pairs) / len(pairs)), 2)
                    corr_max, a, b = max(pairs, key=lambda p: p[0])
                    corr_max = round(float(corr_max), 2)
                    corr_pair = f"{a}–{b}"

            if bench_rets is not None and isinstance(bench_rets, pd.Series):
                both = pd.concat([port, bench_rets], axis=1,
                                 join="inner").dropna()
                if len(both) >= MIN_DAYS:
                    var_b = float(both.iloc[:, 1].var(ddof=1))
                    if var_b > 0:
                        beta = round(float(both.cov().iloc[0, 1] / var_b), 2)

    if not rets:
        short = any(x["rets"] is not None for x in priced)
        var_note = (
            f"riwayat return harian < {MIN_DAYS} hari — "
            "VaR historis/ES tidak dihitung"
            if short else "tidak ada posisi dengan riwayat return harian")
    elif days < MIN_DAYS:
        var_note = (f"irisan tanggal return {days} hari (< {MIN_DAYS}) — "
                    "VaR historis/ES tidak dihitung")
    if risk_contrib is None and rc_note is None:
        rc_note = var_note or "kontribusi risiko tidak tersedia"

    eff_pos = float(max_pos_pct or MAX_POS_PCT)
    eff_sec = float(max_sector_pct or MAX_SECTOR_PCT)
    # peringatan konsentrasi & risiko
    for x in priced:
        if weights[x["ticker"]] > eff_pos:
            warnings.append(
                f"{x['ticker']} {weights[x['ticker']] * 100:.0f}% nilai "
                f"portofolio — di atas batas {eff_pos * 100:.0f}%")
    for s in sector_rows:
        if s["pct"] is not None and s["pct"] / 100 > eff_sec:
            warnings.append(
                f"sektor {s['sector']} {s['pct']:.0f}% — di atas batas "
                f"{eff_sec * 100:.0f}%")
    if mdd is not None and mdd < DD_WARN_PCT:
        warnings.append(f"drawdown berjalan {mdd:.1f}% — di bawah {DD_WARN_PCT:.0f}%")
    if beta is not None and beta > BETA_WARN:
        warnings.append(f"beta {beta:.2f} — lebih agresif dari pasar")
    if corr_max is not None and corr_max > CORR_WARN:
        warnings.append(f"korelasi tinggi {corr_pair} ({corr_max:.2f}) — "
                        "posisi cenderung bergerak bersama")

    inv_vol = (
        inverse_vol_weights(
            quotes, [x["ticker"] for x in priced],
            max_weight=eff_pos, max_sector_pct=eff_sec,
            sectors={x["ticker"]: x["sector"] for x in priced
                     if x["sector"] and x["sector"] != UNKNOWN_SECTOR},
        )
        if len(priced) >= 2
        else None
    )

    return {
        "ok": True,
        "value": round(total),
        "weights": {k: round(v * 100, 1) for k, v in weights.items()},
        "sectors": sector_rows,
        "days": days,
        "vol_ann_pct": vol_ann,
        "max_drawdown_pct": mdd,
        "var95_pct": var95_pct,
        "var95_rp": var95_rp,
        "hist_var95_pct": hist_var_pct,
        "hist_var95_rp": hist_var_rp,
        "hist_es95_pct": hist_es_pct,
        "hist_es95_rp": hist_es_rp,
        "var_n_days": var_n_days,
        "var_breaches": var_breaches,
        "var_expected_breaches": var_expected,
        "var_note": var_note,
        "risk_contrib_pct": risk_contrib,
        "risk_contrib_note": rc_note,
        "beta": beta,
        "corr_avg": corr_avg,
        "corr_max": corr_max,
        "corr_pair": corr_pair,
        "inv_vol": inv_vol,
        "warnings": warnings,
    }


def check_limits(plan: dict, positions: list[dict], modal: float,
                 max_pos_pct: float = MAX_POS_PCT,
                 max_sector_pct: float = MAX_SECTOR_PCT) -> dict:
    """Periksa rencana beli terhadap batas portofolio (P23).

    Nilai posisi lama memakai harga rata-rata (bukan harga pasar) supaya
    pemeriksaan tidak berubah-ubah mengikuti harga; ini disengaja dan
    disebutkan di alasan penolakan bila relevan.
    """
    modal = float(modal or 0)
    if modal <= 0:
        return {"ok": True, "reasons": [], "max_lots": plan.get("lots", 0)}

    ticker = (plan.get("ticker") or "").upper()
    sector = plan.get("sector") or "tidak diketahui"
    proposed = float(plan.get("value") or 0)
    entry = float(plan.get("entry") or 0)

    pos_value = 0.0
    sector_value = 0.0
    for p in positions:
        t = (p.get("ticker") or "").upper()
        v = float(p.get("lots") or 0) * 100 * float(p.get("avg_price") or 0)
        if t == ticker:
            pos_value += v
        if (p.get("sector") or "tidak diketahui") == sector:
            sector_value += v

    reasons: list[str] = []
    pos_limit = modal * max_pos_pct
    sector_limit = modal * max_sector_pct
    if pos_value + proposed > pos_limit + 1e-6:
        reasons.append(
            f"posisi {ticker} sekarang Rp{pos_value:,.0f} + rencana "
            f"Rp{proposed:,.0f} melebihi batas {max_pos_pct * 100:.0f}% modal "
            f"(Rp{pos_limit:,.0f})".replace(",", "."))
    if sector_value + proposed > sector_limit + 1e-6:
        reasons.append(
            f"eksposur sektor {sector} Rp{sector_value:,.0f} + rencana "
            f"Rp{proposed:,.0f} melebihi batas {max_sector_pct * 100:.0f}% "
            f"modal (Rp{sector_limit:,.0f})".replace(",", "."))

    # lot maksimum yang masih lolos kedua batas
    room_pos = max(pos_limit - pos_value, 0.0)
    room_sector = max(sector_limit - sector_value, 0.0)
    room = min(room_pos, room_sector)
    max_lots = int(room // (entry * 100)) if entry > 0 else 0
    return {"ok": not reasons, "reasons": reasons, "max_lots": max_lots,
            "pos_value": round(pos_value), "sector_value": round(sector_value),
            "pos_limit": round(pos_limit), "sector_limit": round(sector_limit)}

def position_fit(ticker: str, positions: list[dict], modal: float,
                 *,
                 max_pos_pct: float = MAX_POS_PCT,
                 max_sector_pct: float = MAX_SECTOR_PCT) -> list[str]:
    """Peringatan bila emiten ini sudah melampaui batas portofolio.

    Dipakai `/cek`: perintah itu tidak mengusulkan ukuran posisi, jadi yang
    bisa dilakukan adalah memberi tahu bila posisi yang sudah ada (dinilai di
    harga rata-rata) melewati batas posisi/sektor.
    """
    modal = float(modal or 0)
    if modal <= 0 or not positions:
        return []
    eff_pos = float(max_pos_pct or MAX_POS_PCT)
    eff_sec = float(max_sector_pct or MAX_SECTOR_PCT)
    t = (ticker or "").upper()
    row = next((p for p in positions
                if (p.get("ticker") or "").upper() == t), None)
    if not row:
        return []
    sector = row.get("sector") or "tidak diketahui"
    value = float(row.get("lots") or 0) * 100 * float(row.get("avg_price") or 0)
    out: list[str] = []
    if value > modal * eff_pos:
        out.append(f"posisi {t} sudah {value / modal * 100:.0f}% modal "
                   f"(batas {eff_pos * 100:.0f}%) — jangan tambah")
    sector_value = sum(
        float(p.get("lots") or 0) * 100 * float(p.get("avg_price") or 0)
        for p in positions
        if (p.get("sector") or "tidak diketahui") == sector)
    if sector_value > modal * eff_sec:
        out.append(f"sektor {sector} sudah {sector_value / modal * 100:.0f}% "
                   f"modal (batas {eff_sec * 100:.0f}%)")
    return out

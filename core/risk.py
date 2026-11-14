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


def inverse_vol_weights(
    quotes: dict[str, dict],
    tickers: list[str],
    *,
    max_weight: float = MAX_POS_PCT,
) -> dict:
    """Target bobot penyeimbang risiko (1 / volatilitas) ala Riskfolio-Lib.

    Hanya menghitung emiten yang punya riwayat return harian >= MIN_DAYS dan
    volatilitas positif; bila < 2 emiten memenuhi syarat, kembalikan ok=False
    tanpa mengarang volatilitas. Menerapkan batas maks per posisi lewat
    water-filling jika jumlah posisi * max_weight >= 100%.
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
            "cap_pct": round(float(max_weight) * 100.0, 1),
            "cap_note": None,
            "reason": "butuh minimal 2 posisi dengan riwayat volatilitas cukup",
        }

    inv = {t: 1.0 / v for t, v in vols.items()}
    total_inv = sum(inv.values())
    w = {t: val / total_inv for t, val in inv.items()}

    cap = float(max_weight or MAX_POS_PCT)
    capped = False
    cap_note = None
    if cap * len(vols) >= 1.0 - 1e-9:
        fixed: dict[str, float] = {}
        free = dict(w)
        while free:
            rem = 1.0 - sum(fixed.values())
            free_sum = sum(free.values())
            scaled = {t: (val / free_sum) * rem for t, val in free.items()}
            over = {t for t, val in scaled.items() if val > cap + 1e-9}
            if not over:
                w = {**fixed, **scaled}
                break
            capped = True
            for t in over:
                fixed[t] = cap
                free.pop(t, None)
        else:  # pragma: no cover - pengaman numerik saat sum(fixed)==1
            w = fixed
    else:
        cap_note = (
            f"batas {cap * 100:.0f}%/posisi belum mengikat untuk "
            f"{len(vols)} posisi (total maks {cap * len(vols) * 100:.0f}% < 100%)"
        )

    return {
        "ok": True,
        "weights": {t: round(val * 100.0, 1) for t, val in w.items()},
        "vols_ann": {t: round(v * math.sqrt(252) * 100.0, 1) for t, v in vols.items()},
        "capped": capped,
        "cap_pct": round(cap * 100.0, 1),
        "cap_note": cap_note,
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
                "value": 0, "weights": {}, "sectors": [], "warnings": []}

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
    days = 0

    if len(rets) < len(priced):
        warnings.append(
            f"{len(priced) - len(rets)} posisi tanpa riwayat cukup — "
            "tidak dihitung di drawdown/korelasi")

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
        inverse_vol_weights(quotes, [x["ticker"] for x in priced], max_weight=eff_pos)
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

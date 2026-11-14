---
name: idx-equity-research
description: >-
  Conduct disciplined, point-in-time equity and fundamental-forensic research on
  IDX/BEI stocks for SahamBot. Use whenever performing deep-dive ticker checks,
  auditing cashflow/dividend-trap quality, synthesizing Bull vs Bear cases,
  updating core/fundamental.py or core/cek.py rules, or reviewing trade plans
  before manual Stockbit execution.
---

# IDX Equity Research Workflow (SahamBot)

Structured single-stock research protocol combining institutional cashflow
forensics (`anthropics/financial-services`, `nichsedge/idx-bei`), point-in-time
citation discipline (`stock-trade-analysis-skills`), and adversarial Bull vs
Bear synthesis (`TradingAgents`, `stock-analyze-skills`).

## Non-Negotiable Mandates

1. **Zero fabrication & explicit observation dates:** Every price, ratio, or
   cashflow metric must carry its data source, `filing_note` (POJK 14/POJK.04/2022
   reporting lag disclosure), and `as_of` date/quarter. Missing metrics stay
   `"tidak tersedia"` — never fill zeros or extrapolate.
2. **Sector-aware & currency-honest thresholds:** Always separate **Bank / Keuangan**
   (`fundamental.is_financial_sector(sector)`) from non-financials before
   judging `DER`, operating cash flow (`OCF`), `FCF`, or `currentRatio`. Read
   `financialCurrency` (fallback `currency`) in `core/fundamental.py` so
   non-IDR issuers (e.g., `USD`-reporting coal/energy stocks) are formatted
   with their native currency in `core/reports.py` (`fmt_amount`, `fmt_compact_amount`)
   rather than blindly labeled `Rp`.
3. **Adversarial balance before any verdict:** Every research note or thesis
   must present **at least 2 Bull facts** AND **at least 2 Bear / risk facts**
   plus a concrete invalidation price (`SL` or thesis-breaker condition).
4. **Honest signal framing:** Technical score (`t_hac = 0.85`, rendered via
   descriptive `trend_label` with disclaimer) and 12-factor relative rank
   (`IC OOS +0.0304`) are cross-sectional filters, never profit guarantees.
   Respect BEI ToS boundaries (no IDX/Stockbit scrapers).

## 5-Pillar Single-Stock Checklist

### 1. Valuation vs Sector Peers & Unit Integrity
- Check `PER`, `PBV`, `ROE`, and net margin (`core/fundamental.py`), compared
  against peer 20d/1y price performance (`core/banding.py`, labeled honestly as
  `"Kinerja harga 20h vs peers"` in `core/cek.py` alongside absolute valuation
  cutoffs: `"Murah (PER & PBV)"` vs `"Campuran (salah satu murah, satunya tinggi)"`
  vs `"Mahal"`).
- **ROE Unit Safety (`roe_unit="pct"`, `_v=2`):** `core/fundamental._fetch_uncached`
  scales raw Yahoo `returnOnEquity * 100.0` once and tags `roe_unit="pct"` (`_v=2`
  in `core/kualitas.py` cache). Always pass `unit=fund_raw.get("roe_unit")` into
  `fundamental.normalize_roe(v, unit=...)` so low positive ROE (`0.2%`–`2.9%`)
  is never multiplied by 100 a second time while legacy untagged decimals (`0.18` -> `18.0%`)
  still normalize cleanly.
- **Exact Valuation & Leverage Vetoes/Warnings:**
  - `core/cek.py`: **`ROE < 2%`** (`0 <= roe < 2`) triggers a hard **`HINDARI` veto** (`"ROE < 2% (laba nyaris nol)"`); negative earnings (`PER < 0` or `ROE < 0`) or `PBV <= 0` also veto `LAYAK`.
  - `core/rencana.py` (`_fund_flags`): **`PBV <= 0`** veto (negative equity), **`PBV > 5`** warning (`"PBV premium"`), **`PER < 0` & `ROE < 0`** veto, **`0 <= ROE < 5%`** warning (`"ROE tipis"`), and **`DER > 300%`** (non-financial) **veto** (`"DER > 300% (utang tinggi)"`).

### 2. Earnings Quality & Cashflow Forensic (`analyze_cashflow_quality`)
For **non-financial** issuers (when Yahoo cashflow/balance fields are present in `core/fundamental.py`):
- **Cash conversion (`ocf_to_ni = OCF / Net Income`):**
  - `OCF < 0` on positive accounting earnings (`net_income > 0`) flags accrual risk (`ocf_negatif_vs_laba`, penalty `+25`); standalone `OCF < 0` adds `+15`.
  - **`ocf_to_ni < 0.7`** flags weak cash conversion (`konversi_kas_lemah`, penalty `+15`); **`ocf_to_ni >= 1.0`** confirms cash-backed profit.
- **Free Cash Flow (`FCF`):** `fcf < 0` flags operating/capex cash burn (`fcf_negatif`, penalty `+15`); `fcf > 0` supports deleveraging or dividends.
- **Balance Sheet Solvency:** Inspect `net_cash = total_cash - total_debt`. When `net_cash <= 0` and **`total_debt > 2 * total_cash`** (`total_cash > 0`), flag high debt-to-cash (`utang_gt_2x_kas`, penalty `+15`). Flag **`current_ratio < 1.0`** (`current_ratio_lt_1`, penalty `+12`; `>= 1.5` healthy).
- **Dividend Trap Screen:** Flag **`payout > 100%`** (`payout_gt_100`, penalty `+20`, all sectors) or high yield (**`div_yield >= 5%`**) paired with **`fcf < 0`** (`dividend_trap_fcf_negatif`, penalty `+15`, non-financials).
- **Score Integration:** `forensic_penalty = min(90, raw_penalty)` directly reduces `score_fundamentals` `pct` and `powerscore.combine` (using `base_pct` to prevent double-counting) so cash-negative or dividend-trap issuers cannot masquerade as `"FUNDAMENTAL KUAT"`.

### 3. Tradability, BEI 2026 Microstructure & Market Regime
- **Liquidity & Board Filter:** Require `value_avg20 >= Rp2 miliar/hari` (`Rp5 miliar+`
  preferred for minimal slippage), exclude `Pemantauan Khusus`, and enforce
  `MIN_PRICE = Rp50` (`core/rencana.py`, `saran.LIQUID_FLOOR_PRICE`) explicitly as a
  **liquidity/illiquid-stock & FCA filter**.
- **BEI 2026 Price Floor & Asymmetric ARB Rules (`core/pricing.py`, `saran.arb_gap_risk`):**
  - **Kep-00136/BEI/09-2026** (effective `2026-09-28`): regular board minimum price floor is **Rp1** (`pricing.min_board_floor`, down from Rp50; Special Monitoring / FCA board is also Rp1). Never state Rp50 as the legal BEI regular floor.
  - **Kep-00158/BEI/10-2026**: **asymmetric ARB = 7%** on the regular board (`pricing.arb_pct`, `pricing.arb_floor_price`; Rp11–14 and Rp1–10 use Rp1 absolute step; FCA board ARB = 10%), while ARA stays tiered (`35%` for Rp11–200, `25%` for >200–5,000, `20%` for >5,000; window status tracked via `pricing.auto_reject_status` and overrideable via `IDX_ARA_WINDOW_END` / `IDX_ARB_WINDOW_END`).
  - **ARB Gap-Risk Check (`pricing.check_arb_gap_risk` / `saran.arb_gap_risk`):** Warn whenever the distance from entry/current price to `SL` exceeds **`7%`** (a single-day ARB lock can trap exits above `SL`, causing multi-day gap slippage) or when price is at/near `arb_floor_price` (`arb_locked`) or zero volume.
- **BEI Tick & Fee Math:** Round Entry/SL/TP to official BEI tick sizes (`pricing.round_tick` / `ceil_tick`), charge `0.40%` all-in round-trip fees (`0.15%` buy + `0.25%` sell), and check ex-dividend dates within `14` days (`core/cek.py`).
- **IHSG Regime Context (`core/regime.py`):** Check causal `^JKSE` `SMA50` vs `SMA200`; halve risk per trade (`1% -> 0.5%`) and allocation caps (`20%/90% -> 10%/45%`) in `core/rencana.py` (`budget_plan` and `finalize`) and `core/saran.py` (`pick` and `fill_pending`) during `DEFENSIVE` regimes.

### 4a. Adversarial Bull vs Bear Debate (Entry Evaluation)
Before issuing `LAYAK` / `TUNGGU` / `HINDARI` or sizing capital:
- **Bull Case (Pro):** Top verified quantitative, cashflow (`ocf_to_ni >= 1.0`, `fcf > 0`, `net_cash > 0`), sector-relative, or catalyst facts supporting upside / resilience.
- **Bear Case (Risiko):** Top forensic (`ocf < 0`, `ocf_to_ni < 0.7`, `total_debt > 2 * total_cash`, dividend trap), valuation, regime, foreign-flow, news, or ARB gap-risk facts that can invalidate the trade.
- **Unified Initial Stop-Loss (`1×ATR` tick-rounded):** `SL = saran.stop_price(entry, atr)` (`1×ATR` rounded to BEI tick size; unified across `core/saran.py`, `core/rencana.py`, `core/screener.py`, and `core/portofolio.py` `STOP_ATR = 1.0`, prioritizing stored execution `sl` from `real_executions` when present).

### 4b. Sell / Thesis-Invalidation / Exit Discipline (`saran.build_exit_guidance`)
When evaluating an existing holding (`/portofolio`, `/jual`, or `/saran` hold review), apply the unified exit priority queue:
1. **Hard Initial / Stored SL (`CUT_LOSS`):** Exit if `close <= sl_initial` (uses stored execution `sl` from `real_executions` if available, else `1×ATR` tick-rounded). Check `arb_locked` first so the user is warned if the order book is locked at ARB `7%`.
2. **Monotonic Chandelier Trailing Exit (`TRAILING_EXIT`, `saran.chandelier_trailing_stop`):** Ratcheting stop `max(prev_stop, High_max(22) - 3.0 * ATR14)` (`CHANDELIER_LENGTH = 22`, `CHANDELIER_MULT = 3.0`, via `pandas_ta_classic.ce` with pure-Python fallback, tick-rounded). Never lowers once raised; triggers `TRAILING_EXIT` when `close <= trailing_sl`.
3. **Full Target (`AMBIL_PROFIT`) & Partial Scale-Out (`SCALE_OUT_TP1`, `saran.scale_out_plan` + `saran.breakeven_stop`):**
   - Full TP sits at `2×ATR` (`saran.target_price`, tick-rounded).
   - At **`TP1 = round_tick(entry + 1.5 * ATR)`** (`>= 2` lots): sell **50% of lots** (`lots_tp1`) and immediately ratchet the remaining runner's SL to **`BEP + 0.40%` BEI fee** (`saran.breakeven_stop(entry) = ceil_tick(entry * 1.0015 / 0.9975)`). For 1-lot positions, raise SL to `BEP + 0.40%` once `TP1` is touched.
4. **Horizontal Time-Stop >= 20 Trading Days (`PERTIMBANGKAN ROTASI/KELUAR`, `saran.time_stop_status` & `hold_review`):**
   - Following López de Prado's vertical barrier (`MAX_HOLD_DAYS = 20` trading days), flag stagnant capital held `>= 20` trading days without hitting TP1/TP or SL.
   - Cross-check cross-sectional rank hysteresis (`HOLD_BAND_PCT = 40.0` / top 60% and `MIN_HOLD_CYCLES = 2`): hold while `rel_pct >= 40` to save `0.40%` rotation fees; rotate when rank drops below top 60% after minimum hold or when fundamental/forensic thesis breaks (`OCF < 0`, `DER > 300%`, `ROE < 2%`).

### 5. Data Quality, Provenance & Post-Trade Accountability
- **OHLCV Quality & Provenance Audit (`core/data_quality.py`, `core/data.py`):**
  - Inspect `data_quality.check(df)` flags (`no_data`, `short_history < 30`, `volume_nol`, `unexplained_jump > 25%`, `bad_ohlc`, `session_gap > 10d`, `stale_days > 5` trading days) and `data_quality.format_provenance(df)`.
  - Verify `df.attrs` provenance metadata populated by `core/data.py` (with per-period disk cache and `yfinance` `repair=True`): `provider` (`yfinance` or opt-in fallback `eodhd`/`alphavantage`), `provenance` (`live`/`cache`/`cache-legacy`/`fallback`), `adjusted=True`, `repaired_count`, and WIB `last_bar_date` / `fetched_at`.
- **Portfolio Limits & Ledger Unity (`core/risk.py`, `core/store.py`, `core/jurnal.py`):**
  - Enforce per-chat custom or default concentration limits (`risk.get_risk_limits` / `risk.check_limits`: default `25%` max position, `40%` max sector) and volatility-balanced weights (`risk.inverse_vol_weights`).
  - `store.add_real_position` and `store.reduce_real_position` keep `/portofolio` and `/jurnal` (`real_executions`) synchronized with `0.40%` fee accounting, while `signal_context` isolates `intent="beli"` vs `intent="jual"`.
  - Audit closed trades in `/jurnal` (`core/jurnal.py` `compute_trade_excursions` & `jurnal_diagnostics`) for realized slippage, **MAE (`mae_r`)**, **MFE (`mfe_r`)**, capture ratio, and net R-multiple after `0.40%` BEI fees.

## Offline Verification Commands
Always run the offline verification suite after updating rules or documentation (never call live network endpoints in tests):
```powershell
.\.venv\Scripts\python.exe tools\check_sync.py
.\.venv\Scripts\python.exe tools\record_fixtures.py --verify
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check .
```

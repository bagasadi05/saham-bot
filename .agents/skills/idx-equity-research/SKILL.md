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
   cashflow metric must carry its data source and `as_of` date/quarter. Missing
   metrics stay `"tidak tersedia"` — never fill zeros or extrapolate.
2. **Sector-aware thresholds:** Always separate **Bank / Keuangan**
   (`fundamental.is_financial_sector(sector)`) from non-financials before
   judging `DER`, operating cash flow (`OCF`), `FCF`, or `currentRatio`.
3. **Adversarial balance before any verdict:** Every research note or thesis
   must present **at least 2 Bull facts** AND **at least 2 Bear / risk facts**
   plus a concrete invalidation price (`SL` or thesis-breaker condition).
4. **Honest signal framing:** Technical score (`t_hac = 0.85`) and 12-factor
   relative rank (`IC OOS ±0.03`) are cross-sectional filters, never profit
   guarantees. Respect BEI toS boundaries (no IDX/Stockbit scrapers).

## 5-Pillar Single-Stock Checklist

### 1. Valuation vs Sector Peers
- Check `PER`, `PBV`, `ROE`, and net margin (`core/fundamental.py`), compared
  against peer 20d/1y returns (`core/banding.py`) rather than raw market-wide
  cutoffs alone.
- Flag value illusions early: low `PER`/`PBV` with `ROE < 2%` or negative
  equity (`PBV <= 0`) is distress, not cheapness.

### 2. Earnings Quality & Cashflow Forensic (`analyze_cashflow_quality`)
For **non-financial** issuers (when Yahoo cashflow/balance fields are present):
- **Cash conversion (`OCF / Net Income`):** `OCF < 0` on positive accounting
  earnings signals accrual risk; `OCF / NI >= 1.0x` confirms cash-backed profit.
- **Free Cash Flow (`FCF`):** Negative `FCF` shows capex/working-capital cash
  burn; positive `FCF` supports deleveraging or dividends.
- **Balance Sheet Solvency:** Inspect `net_cash = totalCash - totalDebt` and
  `currentRatio < 1.0x` for near-term liquidity stress.
- **Dividend Trap Screen:** Flag `payoutRatio > 100%` or high yield (`>= 5%`)
  paired with negative `FCF`.

### 3. Tradability, Microstructure & Market Regime
- **Liquidity & Board:** Require `value_avg20 >= Rp2 miliar/hari` (`Rp5 miliar+`
  preferred for minimal slippage), price `>= Rp50`, and exclude
  `Pemantauan Khusus` (`core/rencana.py`).
- **BEI Execution Math:** Round Entry/SL/TP to official BEI tick sizes
  (`core/pricing.py`), account for `0.40%` all-in fees (`0.15%` buy + `0.25%`
  sell), and note ex-dividend adjustment dates within `14` days (`core/cek.py`).
- **IHSG Regime Context:** Check causal `^JKSE` `SMA50` vs `SMA200`
  (`core/regime.py`); halve risk/allocation budgets during `DEFENSIVE` regimes.

### 4. Adversarial Bull vs Bear Debate
Before issuing `LAYAK` / `TUNGGU` / `HINDARI` or sizing capital:
- **Bull Case (Pro):** Top verified quantitative, cashflow, sector-relative, or
  catalyst facts supporting upside / resilience.
- **Bear Case (Risiko):** Top forensic, valuation, macro/regime, foreign-flow,
  news, or liquidity risks that can invalidate the trade.
- **Invalidation:** Explicit price level (`SL = Entry - 1x ATR`, tick-rounded)
  or fundamental trigger where the thesis is wrong.

### 5. Portfolio Fit & Post-Trade Accountability
- Verify position (`max_pos_pct`, default `25%`) and sector (`max_sector_pct`,
  default `40%`) headroom (`core/risk.check_limits`) plus volatility-balanced
  sizing (`core/risk.inverse_vol_weights`).
- Log real Stockbit fills in `/jurnal` (`core/jurnal.py`) to measure true
  slippage and realized net profit factor vs paper benchmarks.

# Promotion Gates & Quant Pitfalls (`SahamBot`)

## Current Baseline Reference (`data/validation.json`, 2026-11-12)
- Universe: 45 `LQ45_2026` tickers (`2017-09-07` to `2026-10-09`, `91,395` rows, horizon `20` trading days).
- Legacy score `t_hac`: `0.85` (not statistically significant after Newey-West correction).
- Walk-forward 12-factor Ridge: `ic_model_mean = 0.0304` vs `ic_eq_mean = -0.0061` (beats equal-weight `4/5` years; 2025 negative `-0.025`).
- Portfolio (`q=0.2, hold=0.4, step=20`):
  - One-way turnover: `50.6%` (`101.3%` two-way)
  - Gross alpha: `+0.466%` / period -> Net alpha: `+0.264%` / period (`14/20` phases > 0; fee drag ~43% of gross alpha)
  - Moving-block CI95 (`block=4`): `[+0.148, +0.390]%`
  - Deflated Sharpe Ratio: `0.881` (`best_sr=0.246`, `sr0=0.095`, `n_trials=20`)

## Promotion Criteria (All Required to Change Production Rules)
1. **Out-of-sample rank edge:** `ic_model_mean > ic_eq_mean` and beats `ic_eq` in majority of OOS years.
2. **Statistical significance after overlap:** Report `newey_west_t(..., nlags=horizon)` (`t_hac >= 2.0` preferred for new standalone signals).
3. **Positive net alpha after IDX fees (`0.40%` RT):** `alpha_net_mean_pct` improves or holds while moving-block bootstrap CI95 lower bound `lo > 0`.
4. **Honest Multiple-Testing Gate:** Compute `deflated_sharpe(nets, n_trials=total_trials_tested)`. Only claim a statistical edge upgrade when `DSR >= 0.95` under the honest total trial count across all tested configurations (`configs * n_phases` or effective independent trials).
5. **Execution realism:** Check if turnover and implied minimum lot sizes (`tick_size` in `core/pricing.py`) stay practical for retail capital on Stockbit without assuming fractional shares or zero slippage.

## Known IDX Pitfalls (Do Not Repeat)
- **Raw return vs excess return:** Bear markets cause `SELL` clusters to rebound together, creating fake positive raw returns if not demeaned by date.
- **Overlapping 20d daily IC:** Naive `t` assumes independent days; always inspect `t_hac(nlags=horizon)` and non-overlapping `20` phase offsets.
- **Unadjusted prices:** Screener and research must both use `auto_adjust=True`.
- **Single-stock vs cross-sectional:** Cross-sectional Q5 ranking picks *relative* winners among candidates; time-series IC inside one ticker is slightly negative (`-0.0207`).
- **Missing historical membership:** Any backtest on current `LQ45` over 10y retains survivorship bias until `research/bias.py` is fed point-in-time `universe_membership.json` + delisting prices.

# Promotion Gates & Quant Pitfalls (`SahamBot`)

## Current Baseline Reference (`data/validation.json`, generated `2026-11-12` / portfolio `2026-11-14`)
- **Universe & Sample:** 45 `LQ45_2026` tickers (`2017-09-07` to `2026-10-09`, `91,395` rows, horizon `20` trading days).
- **Legacy Technical Score:** Cross-sectional `mean_ic = 0.0115` (naive `t = 2.68`, Newey-West **`t_hac = 0.85`** — not statistically significant after overlap correction); within-ticker time-series `mean_ic = -0.0207` (`t = -2.33`).
- **Walk-Forward 12-Factor Ridge (`choose_lam_cv`):** `ic_model_mean = 0.0304` (`+0.0492 / +0.0490 / +0.0206 / -0.0250 / +0.0584`) vs equal-weight baseline `ic_eq_mean = -0.0061` and legacy score `ic_score_mean = 0.0107` (beats equal-weight in `4/5` OOS years; 2025 negative `-0.025`; `lam_rel = [0.001, 0.003, 0.03, 0.001, 0.03]`).
- **OOS Quintiles (`core/relatif.py` synced to `data/validation.json`):** `Q1 = -0.204%` (win rate `44.0%`), `Q2 = -0.391%`, `Q3 = -0.100%`, `Q4 = +0.292%`, `Q5 = +0.400%` (win rate `47.2%`; Q5−Q1 spread `+0.604%` / 20d).
- **Active Portfolio Baseline (`python research/portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half --offline`, `quintile=0.2`, `step=20`, round-trip cost `0.40%`):**
  - One-way turnover: **`21.7%`** (**`43.4%`** two-way; down from `50.6%`/`101.3%` at `hold=0.4`)
  - Average exposure: **`79.8%`** · Mean Max Drawdown (MDD): **`-20.0%`** (down from `-35.0%` unhedged)
  - Active gross alpha (while invested): **`+0.534%`** / period · Total gross alpha: **`+0.416%`** / period
  - Net alpha after `0.40%` BEI fee: **`+0.33%`** (`+0.330%`) / period (median **`+0.385%`**, **`16/20`** phases net positive; excess vs IHSG `+1.19%` across `20/20` phases)
  - Moving-block bootstrap CI95 (`block=4`, `iters=2000`): **`[+0.243%, +0.522%]`** (`p_positive = 1.0`)
  - Deflated Sharpe Ratio (DSR): **`0.86`** (`best_sr = 0.279`, `sr0 = 0.142`, `n_trials = 20`, `cost_per_round_trip_pct = 0.4`)
  - Full Ablation Trial Count (`experiments_trials`): **`320` trials** (`16` configurations × `20` phases in `research/experiments.py --offline --horizons 20 40 60`; multi-horizon WF IC: `20d = +0.0265` vs `40d = -0.0032` and `60d = -0.0134`)

## Metric Definitions: DSR vs PSR vs P25–P75 IQR (Do Not Conflate)
- **Probabilistic Sharpe Ratio (`PSR`, `probabilistic_sharpe` / `best_phase_psr`):** Probability that a single return stream's Sharpe ratio exceeds a fixed benchmark `SR*` (default `0.0`), adjusting for sample size `T`, skewness, and Pearson kurtosis (`fisher=False`). It does **not** adjust for multiple testing (`n_trials = 1`).
- **Deflated Sharpe Ratio (`DSR`, `deflated_sharpe`):** Adjusts `PSR` for multiple-testing selection bias by setting the benchmark hurdle `sr0 > 0` as the expected maximum Sharpe across `n_trials` independent trials (Bailey & López de Prado, 2014).
  - *Accounting rule:* `data/validation.json` records `deflated_sharpe.n_trials = 20` (the 20 stagger phases of the deployed portfolio rule) AND `experiments_trials.total_trials = 320` (the 16×20 grid in `research/experiments.py`). Neither clears `0.95` (`DSR = 0.86` at `n_trials=20`), so `core/relatif.py` weights remain frozen.
- **Empirical P25–P75 IQR Bands (`summarize_quintile_bands` / `relatif.quintile_dispersion`):** Cross-sectional 25th-to-75th percentile excess return range within each OOS quintile (`Q1..Q5`). Shows stock-level return dispersion around the `+0.400%` Q5 mean — it is **not** a confidence interval of the mean (`ci95_alpha_net`) and **not** a win probability.

## Promotion Criteria (All Required to Change Production Rules)
1. **Out-of-sample rank edge:** `ic_model_mean > ic_eq_mean` and beats `ic_eq` in majority of OOS years.
2. **Statistical significance after overlap:** Report `newey_west_t(..., nlags=horizon)` (`t_hac >= 2.0` preferred for new standalone signals).
3. **Positive net alpha after IDX fees (`0.40%` RT):** `alpha_net_mean_pct` improves or holds while moving-block bootstrap CI95 lower bound `lo > 0`, and active stock-selection alpha (`alpha_active_gross_mean_pct`) is reported separately from cash/regime scaling.
4. **Honest Multiple-Testing Gate:** Compute `deflated_sharpe(nets, n_trials=total_trials_tested)`. Only claim a statistical edge upgrade or unfreeze factor weights when `DSR >= 0.95` under the honest total trial count (`20` phases for single-rule validation; `configs * 20` when selecting across an ablation grid).
5. **Execution realism & live accountability:**
   - Verify `tools/check_sync.py` passes (`relatif_sync`, `lq45_universe`, `data_freshness`, `backup_restore_roundtrip`).
   - Cross-check `/jejak` (`core/scorecard.py` `live_ic` `t_hac` and `BELI` vs `JUAL` `TURUN/NAIK/DATAR` outcomes) and `/jurnal` (`core/jurnal.py` realized slippage, MAE/MFE `mae_r`/`mfe_r`, and net R-multiple after `0.40%` fee) to ensure live fills and tick sizes (`core/pricing.py`) match backtest assumptions.

## Known IDX Pitfalls (Do Not Repeat)
- **Raw return vs excess return:** Bear markets cause `SELL` clusters to rebound together, creating fake positive raw returns if not demeaned by date.
- **Overlapping 20d daily IC:** Naive `t` assumes independent days; always inspect `t_hac(nlags=horizon)` and non-overlapping `20` phase offsets.
- **Unadjusted prices & ex-dividend shifts:** Screener and research must both use `auto_adjust=True` (with `repair=True` in `core/data.py`). In `core/scorecard.py`, `>25%` split jumps are tagged `ADJ`, and `>0.5%` historical close shifts from ex-dividend adjustments are flagged via `div_adjusted`.
- **Single-stock vs cross-sectional:** Cross-sectional Q5 ranking picks *relative* winners among candidates; time-series IC inside one ticker is slightly negative (`-0.0207`).
- **Missing historical membership:** Any backtest on current `LQ45` over 10y retains survivorship bias until `research/bias.py` is fed point-in-time `universe_membership.json` + delisting prices.

---
name: idx-quant-research
description: >-
  Evaluate, improve, and audit quantitative stock-ranking models, turnover/hysteresis
  rules, market regime filters, and feature hypotheses for SahamBot on IDX/BEI
  equities. Use whenever designing alpha experiments, reducing portfolio turnover,
  testing regime filters, modifying core/relatif.py weights, or updating
  data/validation.json.
---

# IDX Quant Research Workflow (SahamBot)

Strict, zero-lookahead protocol to improve net risk-adjusted returns on IDX
equities without overfitting, data-snooping, or violating `AGENTS.md`.

## Non-Negotiable Rules

1. **Causal features only:** Bar `i` uses data `<= i` (including `^JKSE` regime indicators and cross-sectional stats). Target uses `close[i+horizon] / close[i] - 1`.
2. **All-in IDX costs:** Charge `IDX_BUY_FEE = 0.15%` and `IDX_SELL_FEE = 0.25%` (`0.40%` round-trip per side of `|w_new - w_old|`). Do not add `SELL_TAX` separately.
3. **Never cherry-pick a rebalance date:** Always run all `0..step-1` phase offsets in `research/portfolio.py`.
4. **Separate stock-selection alpha from cash beta:** When testing regime filters (cash/half exposure), always report **both** `alpha_active_gross_mean_pct` (stock-selection edge while invested) and total `alpha_net_mean_pct` / Sharpe / max drawdown.
5. **Distinguish DSR, PSR, and IQR dispersion:**
   - **PSR (`probabilistic_sharpe` / `best_phase_psr`):** Tests whether a single phase/strategy Sharpe exceeds a benchmark `SR*` (e.g., `0`) adjusted for sample length, skewness, and Pearson kurtosis (`fisher=False`), *without* multiple-testing penalty.
   - **DSR (`deflated_sharpe`):** Raises the hurdle to `sr0 > 0` across `n_trials` to penalize selection bias across phases/configs.
   - **P25–P75 IQR bands (`summarize_quintile_bands` / `quintile_dispersion`):** Descriptive cross-sectional return dispersion inside each OOS quintile (`Q1..Q5`) — never conflate with CI95 of the mean or with PSR/DSR.
6. **Respect `AGENTS.md` scope boundaries:** No IDX/Stockbit scrapers, no survivorship claims without `universe_membership.json` + delistings (`research/bias.py`), and no profit promises in bot copy.

## Experiment Loop

### 1. Pre-register the Hypothesis & Honest `n_trials` Accounting
Before writing code, define:
- **Economic thesis** (e.g., cutting turnover drag via hysteresis/minimum hold, suppressing bear-regime drawdown, volatility-managed sizing, or horizon alignment).
- **Exact trial count (`n_trials`)** across every configuration tested so `deflated_sharpe()` stays honest:
  - `deflated_sharpe.n_trials = 20` in `data/validation.json` evaluates the 20 non-overlapping rebalance phases of the selected production configuration.
  - `experiments_trials.total_trials = 320` in `research/experiments.py` accounts for the full ablation grid (`16` configurations × `20` phases = `320` trials, plus multi-horizon CV checks).

### 2. Signal, Dispersion & Horizon Check
Inspect single-factor `t_hac` (`nlags=horizon`), OOS quintile P25–P75 IQR bands, and multi-horizon IC before adding complexity:
```powershell
.\.venv\Scripts\python.exe research\horizons.py
.\.venv\Scripts\python.exe research\evaluate.py
```
Check `temp/research/evaluation.md` and `data/validation.json`.

### 3. Purged Out-of-Sample Walk-Forward
- Fit all scalers, cross-sectional z-scores (`zscore_by_date`), and regularization (`choose_lam_cv`) **strictly inside each train split** via `purged_split()` (calendar buffer `ceil(horizon * 7 / 5) + 2 + embargo_days`).
- Compare OOS `ic_model` (`0.0304`) against the equal-weight baseline `ic_eq` (`-0.0061`) and legacy `ic_score` (`0.0107`).

### 4. Multi-Phase Portfolio & Multi-Horizon Ablation Check
Run portfolio evaluation with the active production parameters (`--hold 0.6 --min-hold 2 --regime sma50_200_half --offline`) and multi-horizon ablation (`--horizons 20 40 60`) on `temp/research/oos_predictions.pkl`, then compare against the baseline in `data/validation.json`:
```powershell
.\.venv\Scripts\python.exe research\portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half --offline
.\.venv\Scripts\python.exe research\experiments.py --offline --horizons 20 40 60
```
Inspect `temp/research/experiments.md` and read [references/gates.md](./references/gates.md) for the exact promotion thresholds (`DSR >= 0.95`, PSR, moving-block CI, turnover `21.7%`/`43.4%`, exposure `79.8%`, and drawdown `-20.0%` checks).

### 5. Live Consistency, Realization & Freeze Parity Gate
Before promoting any model/rule change to `core/relatif.py`, `core/saran.py`, or `core/rencana.py`:
1. **Freeze & Sync Parity (`tools/check_sync.py`):** `core/relatif.py` (`WEIGHTS` and `OOS_QUINTILES`) is frozen to the walk-forward CV artifact in `data/validation.json` (`weights_synced_to = "core/relatif.py"`). Do not re-tune weights until a candidate clears `DSR >= 0.95`, and always verify zero drift via `python tools/check_sync.py`.
2. **Live Signal Scorecard (`core/scorecard.py` / `/jejak`):** Verify live out-of-sample realization in `scan_history` — inspect `live_ic()` (`t` and Newey-West `t_hac` with `nlags=horizon`), separate `BELI` (`TP`/`SL`/`TIME` R-multiples) vs `JUAL` accountability (`TURUN`/`NAIK`/`DATAR`, never treated as shorting), and check corporate-action flags (`ADJ` for `>25%` splits, `div_adjusted` for `>0.5%` ex-div shifts on `auto_adjust=True` data).
3. **Real Execution & Excursion Audit (`core/jurnal.py` / `/jurnal`):** Compare realized Stockbit slippage (`avg_slippage_pct`), net win rate / profit factor after `0.40%` BEI fee, and trade excursion diagnostics (`compute_trade_excursions` / `jurnal_diagnostics`: `mae_r`, `mfe_r`, capture ratio, gross vs net R-multiple) against model stop/target assumptions (`1×ATR` SL, `1.5×ATR` TP1 scale-out, `2×ATR` TP, Chandelier trail).
4. **Publish & Run Offline Verification Suite:** Re-generate `data/validation.json` (`python research/publish.py`) and run all offline audit and test gates:
```powershell
.\.venv\Scripts\python.exe research\publish.py
.\.venv\Scripts\python.exe tools\check_sync.py
.\.venv\Scripts\python.exe tools\record_fixtures.py --verify
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check .
```

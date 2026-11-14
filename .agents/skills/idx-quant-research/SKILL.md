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
4. **Separate stock-selection alpha from cash beta:** When testing regime filters (cash/half exposure), always report **both** `alpha_net` (or active stock alpha while invested) and total `net` Sharpe / max drawdown.
5. **Respect `AGENTS.md` scope boundaries:** No IDX/Stockbit scrapers, no survivorship claims without `universe_membership.json` + delistings (`research/bias.py`), and no profit promises in bot copy.

## Experiment Loop

### 1. Pre-register the Hypothesis & `n_trials`
Before writing code, define:
- **Economic thesis** (e.g., cutting turnover drag, suppressing bear-regime drawdown, volatility-managed sizing, or horizon alignment).
- **Exact trial count (`n_trials`)** across every configuration tested so `deflated_sharpe()` stays honest.

### 2. Signal & Horizon Check
Inspect single-factor `t_hac` (`nlags=horizon`) and multi-horizon IC before adding complexity:
```powershell
.\.venv\Scripts\python.exe research\horizons.py
.\.venv\Scripts\python.exe research\evaluate.py
```
Check `temp/research/evaluation.md` and `data/validation.json`.

### 3. Purged Out-of-Sample Walk-Forward
- Fit all scalers, cross-sectional z-scores (`zscore_by_date`), and regularization (`choose_lam_cv`) **strictly inside each train split** via `purged_split()` (calendar buffer `ceil(horizon * 7 / 5) + 2 + embargo_days`).
- Compare OOS `ic_model` against the equal-weight baseline `ic_eq` and legacy `ic_score`.

### 4. Multi-Phase Portfolio & Ablation Check
Run portfolio evaluation and multi-hypothesis ablation on `temp/research/oos_predictions.pkl`, then compare against the baseline in `data/validation.json`:
```powershell
.\.venv\Scripts\python.exe research\portfolio.py --quintile 0.2 --step 20 --hold 0.4
.\.venv\Scripts\python.exe research\experiments.py --offline
```
Inspect `temp/research/experiments.md` and read [references/gates.md](./references/gates.md) for the exact promotion thresholds (`DSR >= 0.95`, moving-block CI, turnover, and drawdown checks).

### 5. Live Consistency & Verification Gate
Before promoting any model/rule change to `core/relatif.py`, `core/saran.py`, or `core/rencana.py`:
1. Confirm `core/relatif.py` `WEIGHTS` matches `data/validation.json` (if weights changed).
2. Cross-check live signal realization via `core/scorecard.py` (`scan_history` / `/jejak`).
3. Re-generate artifacts (`python research/publish.py`) and ensure CI gates pass offline:
```powershell
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check .
```

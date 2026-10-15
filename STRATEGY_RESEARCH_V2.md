# Strategy Research V2

Campaign #1 executed 300 deterministic train/validation configurations on the frozen certified M1 hash. Strategies were EMA crossover, RSI momentum, RSI mean reversion, Donchian breakout, and Bollinger reversion, sliced across four sessions and three regimes. The best validation slice was Bollinger reversion / New York / Bear Trend: 222 trades, 46.85% wins, expectancy -0.0156R, PF 0.944, max drawdown -15.96R.

Because no validation configuration had positive expectancy and PF > 1, no finalist was frozen and the final test remained untouched. No strategy was promoted.

Machine-readable results: `strategy_research_v2/CAMPAIGN1_RESULTS.json` and `EXPERIMENT_LEADERBOARD.csv`.

## Campaign #2

The isolated `campaign_v2_02` run completed 96 ATR/triple-barrier configurations across trend-pullback, range mean-reversion, and Donchian breakout strategies. ATR periods 7/14/21/28 and controlled SL/TP/holding combinations were evaluated on train and validation. Zero candidates met the gate of at least 100 trades, positive expectancy, and PF above 1. The final test was not accessed.

Results: `campaign_v2_02/CAMPAIGN2_RESULTS.json` and `campaign_v2_02/EXPERIMENT_REGISTRY.csv`.

## Campaign #3

Campaign #3 generated 41,115 setup events and 41,115 meta-labels (14,631 positive, 26,484 negative) across six deterministic setup generators. Logistic Regression, Random Forest, and Gradient Boosting were screened at five validation thresholds. The run produced a Random Forest selection at threshold 0.50, but its reported R normalization is numerically unstable and the result is rejected pending metric audit; it is not a finalist. The final test was not accessed.

Results: `campaign_v2_03/CAMPAIGN3_RESULTS.json` and `campaign_v2_03/EXPERIMENT_REGISTRY.json`.

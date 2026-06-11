# Strategy Refinement Playbook

Use this playbook to refine the paper-trading strategies without letting automation rewrite strategy logic. The goal is to turn saved paper evidence into small, human-reviewed changes that can be measured over the next several post-market snapshots.

## Safety Rules

- Work on `develop` unless explicitly promoting a proven change.
- Keep `ALPACA_PAPER=true` and `AUTO_SUBMIT_REQUIRES_PAPER=true`.
- Every trade must flow through a previewed `order_intent`.
- `strategy_change_suggestions` and `strategy_tuning_decisions` are review records only. They do not apply config changes automatically.
- Tune one narrow thing at a time: one scanner type, one symbol or profile, one or two config keys.
- Do not tune from a single trade unless it exposed a mechanical failure, such as all contracts being rejected for the same avoidable reason.

## System Flow

The active paper system is:

```text
strategy.config.scanner
-> evaluator-backed signal scan
-> Signal
-> option contract selection and order_intent preview
-> optional paper submit
-> broker reconciliation imports fills and positions
-> exit evaluation
-> post-market maintenance
-> trade_cases, review_snapshots, ai_trade_reviews, strategy_refinement summary
```

The data used for refinement lands in these places:

- `signals`: signal volume, status, market context, preview failures.
- `option_selection_diagnostics`: rejected contract candidate reason counts.
- `order_intents`: previewed/submitted/rejected order intent state.
- `fills`: Alpaca paper fills from reconciliation.
- `trade_cases`: closed FIFO round trips built from fills.
- `review_snapshots.raw_payload.learning_report`: daily saved learning report, including `refinement_candidates`.
- `strategy_tuning_decisions`: human-recorded tuning decisions, evidence, expected effect, and later outcome summary.

## Daily Evidence Workflow

Post-market maintenance now builds the daily evidence bundle. After it runs, inspect:

```http
GET /api/v1/automation/strategy-refinement
GET /api/v1/automation/learning-report
GET /api/v1/automation/review-snapshots?limit=10
GET /api/v1/automation/strategy-change-suggestions?status=pending
GET /api/v1/automation/strategy-tuning-decisions
```

Recommended default refinement query:

```http
GET /api/v1/automation/strategy-refinement?days=10&min_closed_trade_cases=5&min_rejected_previews=10&min_no_signal_reasons=20&limit=50
```

Interpret readiness statuses this way:

| Status | Meaning | First action |
|---|---|---|
| `not_enough_data` | The group lacks enough closed trades, rejected previews, or no-signal observations. | Do not tune yet unless there is a clear mechanical bug. |
| `watch` | Evidence exists, but no strong tuning pressure is present. | Keep collecting. |
| `ready_for_review` | Evidence meets a gate and has non-monitor focus. | Review snapshots and record a decision before changing config. |
| `needs_option_filter_review` | Preview or option diagnostics dominate. | Tune preview/profile/option selector first, not the signal evaluator. |
| `needs_signal_threshold_review` | No-signal pressure dominates. | Tune scanner thresholds, timeframe, lookback, or dedupe. |
| `needs_exit_rule_review` | Closed trade losses/risk outcomes dominate. | Tune exit or risk controls before entry logic. |

## Refinement Decision Loop

1. Pick the highest-priority `strategy-refinement` candidate with minimum evidence met.
2. Classify the problem:
   - Signal threshold problem
   - Option-selection problem
   - Exit/risk problem
   - Runtime/schedule/data problem
3. Read the supporting snapshot IDs and recent candidates.
4. Record a human decision before applying any config change:

```http
POST /api/v1/automation/strategy-tuning-decisions
```

Example payload:

```json
{
  "scanner_type": "moving_average",
  "symbol": "SPY",
  "decision_type": "tighten_spread_filter",
  "description": "SPY moving-average previews are accepting too many wide-spread contracts.",
  "expected_effect": "Fewer bad fills and fewer loss-heavy trade cases from wide spreads.",
  "proposed_config_patch": {
    "scanner": {
      "preview": {
        "max_spread_percent": "25"
      }
    }
  },
  "evidence_snapshot_ids": ["snapshot-id-1", "snapshot-id-2"],
  "evidence_summary": {
    "preview_rejected": 14,
    "dominant_reason": "spread_too_wide"
  },
  "created_by": "admin"
}
```

5. Apply the actual config change manually through the normal strategy/env update path.
6. Wait for enough post-market snapshots.
7. Review the decision's before/after window in `GET /api/v1/automation/strategy-refinement`.
8. Update the decision with outcome:

```http
PATCH /api/v1/automation/strategy-tuning-decisions/{id}
```

Example:

```json
{
  "status": "applied",
  "outcome_summary": {
    "after_snapshot_count": 5,
    "result": "improved",
    "notes": "Priority score fell and realized P/L improved after reducing max spread percent."
  }
}
```

## Problem Classification

### Signal Threshold Problem

Symptoms:

- `strategy-refinement` status is `needs_signal_threshold_review`.
- `no_signal.reasons_seen` is high.
- `signals.seen` is low relative to scan cycles.
- No-signal reasons mention an evaluator producing no signal.

Likely causes:

- Threshold too strict.
- Lookback too short or too long for the symbol.
- Timeframe too noisy or too slow.
- Direction filter blocks half of valid setups.
- Dedupe suppresses too many repeated signals.

Do:

- Loosen scanner threshold slightly.
- Extend lookback if indicators lack enough context.
- Increase timeframe to reduce noise, or decrease timeframe to capture shorter moves.
- Reduce `dedupe_minutes` only when duplicate suppression is hiding legitimately separate setups.

Do not:

- Loosen option spread/OI filters just because no signals were generated.
- Change multiple scanner keys at once.

### Option-Selection Problem

Symptoms:

- `strategy-refinement` status is `needs_option_filter_review`.
- `option_selection.reason_counts` has dominant rejection reasons.
- `signals.preview_rejected` is high.
- Good signal volume exists, but previews do not become orders.

Likely causes:

- Candidate limit too low.
- DTE window too narrow.
- Target strike too far from liquid strikes.
- `min_open_interest` too high for the symbol/feed.
- Spread caps too tight for paper feed quotes.
- Notional cap too low for the selected underlying.

Do:

- First raise `OPTIONS_CANDIDATE_LIMIT` only if diagnostics show too few candidates evaluated.
- Tune per-profile env vars such as `PREVIEW_PROFILE_<PROFILE>_MAX_SPREAD_PERCENT`, `MIN_OPEN_INTEREST`, or `MAX_ESTIMATED_NOTIONAL`.
- Adjust DTE windows before loosening liquidity filters if contracts are too near expiration.
- Review missing-OI allowlist only after confirming OI is structurally absent for the feed.

Do not:

- Treat `spread_too_wide` as a signal failure.
- Loosen both spread and notional caps in the same pass.
- Add single-name symbols to `OPTIONS_ALLOW_MISSING_OI_SYMBOLS` without evidence.

### Exit And Risk Problem

Symptoms:

- `strategy-refinement` status is `needs_exit_rule_review`.
- Closed trade cases meet the evidence gate and losses dominate.
- Entries look reasonable, but winners reverse or losses grow.
- Open positions survive until DTE exits too often.

Relevant exit logic:

- `profit_target_percent` exits when unrealized P/L percent is at or above target.
- `stop_loss_percent` exits when unrealized P/L percent is at or below negative stop.
- `max_days_to_expiration` exits when expiration is N or fewer calendar days away.
- `max_hold_hours` exits after a configured holding duration.
- Exit limit price source is `bid` or `midpoint`.

Do:

- Lower profit target if winners often fade before reaching target.
- Tighten stop loss if losing trades are large and quick.
- Add or lower `max_hold_hours` if signals decay intraday.
- Raise DTE buffer if positions are being managed too close to expiration.

Do not:

- Tighten entry filters before checking whether exits are the real loss source.
- Use exit changes to compensate for bad option liquidity.

### Runtime Or Schedule Problem

Symptoms:

- Job failures appear in learning report.
- No-signal reasons show no usable bars across symbols.
- Reconciliation/fill data is missing.
- Maintenance snapshots are stale.

Do:

- Check `job_runs` and Render logs first.
- Confirm cron hours around DST.
- Confirm Alpaca credentials and paper mode.
- Confirm post-market maintenance created `review_snapshots`.

Do not:

- Tune strategy logic when the data pipeline is unhealthy.

## Active Strategy Mechanics

### `moving_average`

Intent:

- Trend-following setup from a short average against a long average.

Default seed shape:

- Timeframe: `5Min`
- Lookback: `1440` minutes
- Short window: `5`
- Long window: `20`
- Average type: `ema` unless configured otherwise
- Trigger commonly seeded as `bullish_cross`
- Requires price confirmation and short-average slope by default

Signal logic:

- Bullish crossover trigger requires previous short average at/below long average and latest short above long average.
- Bearish crossover trigger requires previous short average at/above long average and latest short below long average.
- Trend-state trigger only requires short average above/below long average.
- Bullish setup normally requires current price above short average and positive short slope.
- Bearish setup normally requires current price below short average and negative short slope.
- `min_change_percent` filters out weak latest-candle movement.
- Optional `min_average_separation_percent` rejects weak average separation.
- Optional `max_price_distance_percent` rejects entries stretched too far from the short average.

Tune first:

- Too few signals: lower `min_change_percent`, allow trend-state instead of cross-only, reduce `short_window`, increase `lookback_minutes`.
- Too many late entries: add or lower `max_price_distance_percent`, raise `min_average_separation_percent`, require crossover trigger.
- Whipsaw losses: increase timeframe, increase long window, require larger separation.

Avoid:

- Lowering `dedupe_minutes` before checking signal quality.

### `momentum_rate_of_change`

Intent:

- Short-term price momentum over a lookback window.

Default seed shape:

- Timeframe: `1Min`
- Lookback: `30` minutes
- Bullish threshold: `change_above_percent`
- Bearish threshold: `change_below_percent`
- Short average filter: `ema`, window `9`
- Requires latest candle confirmation by default

Signal logic:

- Calculates percent change from the lookback reference close to latest close.
- Bullish signal when percent change is at or above `change_above_percent`.
- Bearish signal when percent change is at or below `change_below_percent`.
- Latest candle must confirm direction when enabled.
- Latest price must be on the correct side of the short average.
- Optional `max_extension_percent` rejects overextended moves.

Tune first:

- Too few signals: reduce absolute change thresholds, reduce lookback, loosen latest candle confirmation carefully.
- Losses after chasing: add or reduce `max_extension_percent`, increase lookback, raise threshold.
- Good signals but missed previews: tune option selection, not momentum thresholds.

Avoid:

- Raising thresholds and shortening lookback together. That can create sparse, late signals.

### `rsi_reversal`

Intent:

- Mean-reversion style signal after RSI leaves an extreme.

Default seed shape:

- Timeframe: `5Min`
- RSI period: `14`
- Oversold: `35`
- Overbought: `65`
- Confirmation mode: `cross_back_inside`
- Requires price confirmation by default

Signal logic:

- Bullish signal when previous RSI is below oversold and latest RSI crosses back above oversold.
- Bearish signal when previous RSI is above overbought and latest RSI crosses back below overbought.
- `reversal_candle` mode allows improving RSI with reversal candle before full cross back inside.
- Optional trend average can reject trend conflicts if `reject_trend_conflict` is enabled.

Tune first:

- Too few signals: move oversold higher or overbought lower, use `reversal_candle`, reduce timeframe.
- Too many bad countertrend trades: enable trend conflict rejection, use stricter extreme levels such as 30/70, increase timeframe.
- Winners fade: review exit targets and max hold.

Avoid:

- Making RSI levels too loose on strongly trending symbols without trend filtering.

### `macd_crossover`

Intent:

- Momentum/trend shift when MACD crosses its signal line.

Default seed shape:

- Timeframe: `5Min`
- Fast/slow/signal: `12/26/9`
- Requires price confirmation
- Seed disables histogram confirmation in templates, though evaluator can require it

Signal logic:

- Bullish signal when MACD crosses above signal line.
- Bearish signal when MACD crosses below signal line.
- Optional price confirmation requires latest close to move in direction.
- Optional histogram confirmation requires histogram on correct side of zero.
- Confidence improves when histogram expands and MACD is above/below zero in the signal direction.

Tune first:

- Too few signals: disable histogram confirmation, shorten fast/slow periods, lower timeframe.
- Too many noisy crossovers: enable histogram confirmation, increase timeframe, lengthen periods.
- Late entries: reduce timeframe or shorten periods, but monitor whipsaw.

Avoid:

- Treating MACD no-signal volume as option selection trouble.

### `mean_reversion`

Intent:

- Bollinger Band touch followed by close back inside the band.

Default seed shape:

- Timeframe: `5Min`
- Bollinger period: `20`
- Stddev: `2.0`
- Optional `max_distance_to_middle_percent`
- Requires price confirmation

Signal logic:

- Bullish signal when latest low dips below lower band and latest close returns above lower band.
- Bearish signal when latest high moves above upper band and latest close returns below upper band.
- Candle confirmation checks close improvement or open/close direction.
- Optional max distance to middle rejects trades too far from mean.

Tune first:

- Too few signals: reduce Bollinger stddev, reduce period, loosen max distance.
- Too many falling-knife losses: require stronger price confirmation, enable trend filter at strategy level if added later, use stricter bands.
- Profitable but exits too early/late: tune profit target/stop/max hold.

Avoid:

- Using very loose bands and loose exits together.

### `breakout_price_threshold`

Intent:

- Price breaks above/below a configured or recent range level.

Default seed shape:

- Timeframe: `5Min`
- Lookback: `480` minutes
- Range lookback: `20` candles
- Breakout buffer: `0.05`
- Max breakout distance: `3.0`
- Requires price confirmation

Signal logic:

- Uses configured `price_above`/`price_below` if present.
- Otherwise derives recent range high/low from prior candles.
- Bullish requires previous close at/below level and latest close above buffered level.
- Bearish requires previous close at/above level and latest close below buffered level.
- Candle confirmation requires directional close/open behavior.
- Optional max breakout distance rejects overextended breakouts.

Tune first:

- Too few signals: lower buffer, shorten range lookback, loosen max distance.
- False breakouts: increase buffer, require volume-confirmed breakout instead, increase timeframe.
- Entries too late: lower max distance, review candidate DTE and spread.

Avoid:

- Loosening breakout buffer and max distance in the same pass.

### `volume_confirmed_breakout`

Intent:

- Breakout only when volume confirms.

Default seed shape:

- Timeframe: `5Min`
- Range lookback: `20`
- Volume lookback: `20`
- Min relative volume: `1.25`
- Breakout buffer: `0.05`
- Requires candle confirmation

Signal logic:

- Derives or uses breakout levels like `breakout_price_threshold`.
- Requires latest volume divided by recent average volume to meet `min_relative_volume`.
- Bullish candle close should be high in its range.
- Bearish candle close should be low in its range.
- Optional max breakout distance rejects overextension.

Tune first:

- Too few signals: reduce `min_relative_volume`, shorten volume lookback, lower close-position requirement if added.
- False breakouts: raise `min_relative_volume`, increase buffer, raise timeframe.
- Good signals but low fills: tune option profile.

Avoid:

- Reducing volume requirement below ordinary noise unless collecting data intentionally.

### `volatility_squeeze`

Intent:

- Bollinger Band compression followed by range breakout.

Default seed shape:

- Timeframe: `5Min`
- Lookback: `720` minutes
- Bollinger period/stddev: `20` / `2.0`
- Squeeze lookback: `20`
- Range lookback: `20`
- Compression ratio threshold: `0.90`
- Breakout buffer: `0.05`
- Max breakout distance: `4.0`
- Requires price confirmation

Signal logic:

- Computes Bollinger Band width as percent of middle band.
- Compression is detected if recent minimum band width is below configured max width or below average width times compression ratio.
- Bullish signal breaks above range high after compression.
- Bearish signal breaks below range low after compression.
- Width expansion adds confidence.

Tune first:

- Too few signals: raise compression ratio threshold, raise max band width if used, shorten squeeze lookback.
- Too many weak breakouts: lower compression ratio threshold, increase breakout buffer, require tighter max distance.
- Late entries: reduce range lookback or max distance.

Avoid:

- Making compression too easy and breakout buffer too low together.

### `support_resistance`

Intent:

- Trade bounces, rejections, breakouts, or breakdowns around support/resistance levels.

Default seed shape:

- Timeframe: `5Min`
- Lookback: `720` minutes
- Mode: `both`
- Swing window: `3`
- Lookback candles: `60`
- Min touches: `2`
- Tolerance: `0.20`
- Breakout buffer: `0.075`
- Max distance: `1.0`
- Requires candle confirmation

Signal logic:

- Uses configured `support_levels` and `resistance_levels` if present.
- Otherwise builds swing high/low levels and clusters nearby levels by tolerance.
- Breakout mode:
  - Resistance breakout is bullish.
  - Support breakdown is bearish.
- Rejection/bounce mode:
  - Support bounce is bullish.
  - Resistance rejection is bearish.
- Requires minimum touches.
- Optional max distance rejects trades too far from the level.

Tune first:

- Too few signals: lower min touches, widen tolerance, reduce swing window.
- Too many low-quality levels: raise min touches, narrow tolerance, use manual levels.
- Breakouts failing: increase breakout buffer or mode-filter to rejection only.

Avoid:

- Widening tolerance and lowering min touches at the same time.

## Cross-Strategy Tuning Levers

### Timeframe

Lower timeframe:

- More signals.
- Faster response.
- More noise and whipsaw.

Higher timeframe:

- Fewer signals.
- Slower but cleaner confirmations.
- Can miss intraday moves.

### Lookback

Shorter lookback:

- More reactive.
- Less context.

Longer lookback:

- More stable indicators.
- Slower reaction.

### Dedupe

`dedupe_minutes` suppresses repeated active signals by symbol, scanner, signal type, and direction while statuses are `new`, `previewed`, `submitted`, `signal_only`, or `preview_disabled`.

Tune dedupe only when:

- A strategy has good signals but misses separate legitimate setups.
- The symbol is high-frequency enough to produce multiple independent events.

Do not lower dedupe to create volume if signal quality is weak.

### Direction

Seeded strategies are global per scanner type. They scan the configured symbol universe and let signal direction choose the option side:

- Bullish signals preview calls.
- Bearish signals preview puts.
- Tune the scanner/profile first. Include direction in the evidence summary when one side is clearly underperforming.

## Option Selection Tuning

The option selector has two stages:

1. Candidate retrieval and ranking:
   - DTE window.
   - Strike proximity to target or underlying.
   - Open interest availability.
   - Candidate cap.
2. Quote and liquidity checks:
   - Open interest.
   - Bid/ask availability.
   - Quote size.
   - Estimated notional.
   - Absolute spread OR relative spread.

Primary global settings:

| Setting | Default | Purpose |
|---|---:|---|
| `OPTIONS_MIN_DTE` | `7` | Default minimum DTE when strategy config does not override. |
| `OPTIONS_TARGET_DTE` | `14` | Preferred DTE in ranking. |
| `OPTIONS_MAX_DTE` | `45` | Default maximum DTE. |
| `OPTIONS_CANDIDATE_LIMIT` | `100` | Candidate breadth. |
| `OPTIONS_MAX_SPREAD_PCT` | `0.15` | Relative spread cap. |
| `OPTIONS_MIN_OPEN_INTEREST` | `50` | Default OI floor. |
| `OPTIONS_ALLOW_MISSING_OI_SYMBOLS` | `SPY,QQQ` | Missing-OI allowlist. |

Per-profile settings follow:

```text
PREVIEW_PROFILE_<PROFILE>_<SETTING>
```

Examples:

```text
PREVIEW_PROFILE_MOVING_AVERAGE_MIN_OPEN_INTEREST=50
PREVIEW_PROFILE_MOVING_AVERAGE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MOVING_AVERAGE_MAX_SPREAD_PERCENT=35
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MIN_OPEN_INTEREST=50
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MAX_ESTIMATED_NOTIONAL=5000
```

Current paper profile notional caps by strategy type:

| Profile | Max estimated notional |
|---|---:|
| `moving_average` | `5000` |
| `momentum_rate_of_change` | `5000` |
| `rsi_reversal` | `5000` |
| `macd_crossover` | `5000` |
| `mean_reversion` | `5000` |
| `breakout_price_threshold` | `5000` |
| `volume_confirmed_breakout` | `5000` |
| `volatility_squeeze` | `5000` |
| `support_resistance` | `5000` |
| `vwap_reclaim` | `5000` |
| `opening_range_breakout` | `5000` |
| `relative_strength` | `5000` |
| `time_series_momentum` | `5000` |
| `market_regime_filter` | `5000` |
| `pairs_relative_value` | `5000` |
| `options_spread_candidate` | `5000` |

The May 18 strategy-type batch tunes scanner behavior before removing symbols from the universe. SPY remains enabled; use SPY outcomes as evidence for scanner/profile tuning unless a future decision explicitly approves a symbol removal.

The current seeded posture is `strictness_profile=selective_winner_bias` with `strictness_level=0.70`. New or reseeded strategies should prefer stronger thresholds and longer dedupe over raw signal volume, while keeping seeded submit caps high enough to allow good signals through. Use runtime env caps for global paper-safety limits.

New strategy types added after the May 18 review:

| Scanner | Tuning focus |
|---|---|
| `vwap_reclaim` | Reclaim/rejection distance from VWAP and dedupe. |
| `opening_range_breakout` | Opening range length, breakout buffer, and max distance. |
| `relative_strength` | Cross-sectional edge versus the active paper universe. |
| `time_series_momentum` | Longer lookback trend return and trend-average filter. |
| `market_regime_filter` | Benchmark alignment using SPY/QQQ regime returns. |
| `pairs_relative_value` | Spread threshold versus configured peer benchmark; signal-only until paired execution support exists. |
| `options_spread_candidate` | Spread-worthy directional setup; signal-only until multi-leg order support exists. |

Apply the current strategy-type batch manually after recording the approved decisions:

```powershell
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-05-18-strategy-type-batch --dry-run
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-05-18-strategy-type-batch
```

Batch scope:

| Scanner | Patch intent |
|---|---|
| `support_resistance` | Require better level quality and closer entries. |
| `momentum_rate_of_change` | Reduce chase entries and test a wider stop with stricter entry filtering. |
| `moving_average` | Tighten trend quality and reduce duplicate entries. |
| `mean_reversion` | Require cleaner band setup and test a wider stop with stricter entries. |
| `rsi_reversal` | Use stricter RSI extremes and reject trend conflicts. |
| `volume_confirmed_breakout` | Require stronger volume and less extended breakouts. |
| `macd_crossover` | Watch only for now. |
| `breakout_price_threshold` | Watch only for now. |
| `volatility_squeeze` | Watch only for now. |

Apply the 2026-06-11 fresh-paper tuning batch manually after recording the approved decisions:

```powershell
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-06-11-fresh-paper-tuning-batch --dry-run
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-06-11-fresh-paper-tuning-batch
```

Batch scope:

| Scanner | Patch intent |
|---|---|
| `mean_reversion` | Require a cleaner Bollinger setup and a closer return toward the middle band. |
| `momentum_rate_of_change` | Require stronger ROC evidence and reject more extended moves. |
| `support_resistance` | Require entries closer to the active level. |
| `time_series_momentum` | Require a stronger longer-horizon trend. |

Common diagnostic reasons:

| Reason | Meaning | First tuning check |
|---|---|---|
| `missing_open_interest` | OI is null and symbol is not allowlisted. | Confirm feed behavior before allowlisting. |
| `low_open_interest` | OI below threshold. | Lower profile OI only if quote quality is strong. |
| `missing_quote` | No quote returned. | Increase candidate breadth or DTE window. |
| `no_usable_two_sided_quote` | Bid/ask missing or zero. | Avoid loosening unless paper feed issue is confirmed. |
| `quote_unavailable` | Alpaca quote fetch failed. | Treat as data/runtime issue first. |
| `estimated_notional_above_max` | Contract too expensive. | Raise profile notional or target lower premium strikes. |
| `spread_too_wide` | Both absolute and relative spread caps failed. | Tune spread percent cautiously. |
| `quote_size_too_low` | Bid/ask size below floor. | Lower only for diagnostics, not performance. |
| `not_tradable` | Contract inactive or not tradable. | Do not tune strategy. |
| `no_expiration_strike_match` | DTE/strike filters returned no contracts. | Adjust DTE/target strike/candidate breadth. |

## Exit Tuning

Default seeded exit config:

- `profit_target_percent`: from `STRATEGY_PROFIT_TARGET_PERCENT`, default `25`.
- `stop_loss_percent`: from `STRATEGY_STOP_LOSS_PERCENT`, default `10`.
- `stop_loss_min_dollars`: from `STRATEGY_STOP_LOSS_MIN_DOLLARS`, default `10`.
- `max_days_to_expiration`: `1`.
- Limit price source: `bid`.
- Exit submit is enabled for sell orders.

The stop loss triggers only when both the percent loss threshold and minimum dollar loss floor are met. For example, a position down 50% but only down $5 does not trigger a stop when `stop_loss_min_dollars=10`.

Existing strategies can be patched manually:

```powershell
.\.venv\Scripts\python.exe scripts\update_strategy_stop_loss.py --dry-run
.\.venv\Scripts\python.exe scripts\update_strategy_stop_loss.py
```

Exit tuning examples:

| Symptom | Change to test |
|---|---|
| Winners often reverse before target | Lower profit target or add max hold hours. |
| Losses are large and fast | Tighten stop loss. |
| Options decay into expiration | Increase DTE exit buffer. |
| Exits preview but do not fill | Compare bid vs midpoint limit price source and spread. |
| Too many early exits | Widen stop or target only after entry quality is confirmed. |

## Evidence Thresholds

Default strategy-refinement gates:

- `min_closed_trade_cases=5`
- `min_rejected_previews=10`
- `min_no_signal_reasons=20`

Suggested stricter review gates:

- For scanner logic changes: 20 or more no-signal observations or 10 or more closed cases.
- For option filter changes: 10 or more rejected previews with one dominant reason.
- For exit changes: 10 or more closed trade cases, unless a single catastrophic behavior is obvious.

## Recommended Tuning Order

Use this order unless the data clearly says otherwise:

1. Runtime health
2. Option-selection mechanics
3. Exit/risk controls
4. Scanner thresholds
5. Strategy universe or schedule

Reason:

- Runtime failures can make all evidence false.
- Option selection can block good signals from becoming measurable trades.
- Bad exits can make good entries look bad.
- Scanner tuning is most useful after orders/fills/exits are healthy.

## Recording A Tuning Decision

Record a decision whenever a change is made:

```http
POST /api/v1/automation/strategy-tuning-decisions
```

Include:

- `scanner_type`
- `symbol`
- `decision_type`
- `description`
- `expected_effect`
- `proposed_config_patch`
- `evidence_snapshot_ids`
- `evidence_summary`
- `created_by`

Use statuses:

- `approved`: decision is approved for manual application.
- `applied`: change was actually applied.
- `rejected`: decision was reviewed and rejected.
- `archived`: no longer relevant.

Never use this record as an executor. It is evidence and memory, not automation.

## Examples

### Example 1: Moving Average Has Many Preview Rejections

Evidence:

- `needs_option_filter_review`
- `preview_rejected=18`
- Dominant diagnostic reason: `spread_too_wide`
- Closed trade cases are too few to judge entry quality.

Decision:

- Do not change moving average windows.
- Tighten or loosen spread only after reviewing whether accepted contracts were too wide or all contracts were rejected.
- If the goal is collecting data, loosen profile spread slightly.
- If the goal is better fill quality, tighten profile spread and accept fewer trades.

Record:

```json
{
  "scanner_type": "moving_average",
  "symbol": "SPY",
  "decision_type": "adjust_preview_spread_filter",
  "expected_effect": "Reduce preview friction while preserving quote quality."
}
```

### Example 2: Momentum Has Losses With Enough Closed Trades

Evidence:

- `needs_exit_rule_review`
- `closed_trade_cases=12`
- `losing_trade_cases=8`
- Priority score worsening.
- Option diagnostics low.

Decision:

- Check whether losses are fast reversals or slow decay.
- If fast reversals: tighten stop loss or add max hold.
- If winners fade: lower profit target or add trailing-style logic later.
- Do not loosen momentum threshold until exit behavior is understood.

### Example 3: RSI Reversal Produces Too Few Signals

Evidence:

- `needs_signal_threshold_review`
- High no-signal reasons.
- Few preview failures.
- Few closed trades.

Decision:

- Move oversold/overbought from 30/70 toward 35/65, or use `reversal_candle`.
- Keep option profile unchanged.
- Require trend conflict rejection if losses appear after volume increases.

## Review Checklist

Before changing anything:

- Is post-market maintenance current?
- Are there recent `review_snapshots`?
- Does `strategy-refinement` show minimum evidence met?
- Is the problem signal, option selection, exit/risk, or runtime?
- Is the change one scanner/profile and one or two keys?
- Has a `strategy_tuning_decision` been recorded?
- Is there a clear expected effect?

After changing:

- Wait for enough new snapshots.
- Compare before/after priority score.
- Compare realized P/L and closed trade count.
- Compare preview rejections and option diagnostic reasons.
- Update the tuning decision outcome.
- Revert or adjust if priority worsens for several snapshots.

## Useful Commands

Run signal tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/services/signals
```

Run refinement tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_learning_report.py tests/test_strategy_refinement.py tests/test_strategy_refinement_routes.py
```

Check migration head:

```powershell
.\.venv\Scripts\alembic.exe heads
```

Expected current head:

```text
0011_strategy_tuning_decisions
```


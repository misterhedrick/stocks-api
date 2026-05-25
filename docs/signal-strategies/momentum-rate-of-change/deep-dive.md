# Momentum / rate-of-change implementation deep dive

## Implementation objective

Build a lightweight continuation scanner that detects symbols with meaningful directional movement over a configurable lookback window, while rejecting late, exhausted, or one-candle reversal moves.

This strategy should be one of the fastest evaluators because it mostly needs close prices, recent candles, and optional moving-average/market-regime context.

## Required data

Minimum candles:

```text
timestamp, open, high, low, close, volume optional
```

Required derived values:

```text
current close
lookback start close
percent change over lookback
latest candle direction
short moving average optional
average absolute move optional
market regime optional
```

## Config schema proposal

```json
{
  "type": "momentum_rate_of_change",
  "symbols": ["SPY"],
  "timeframe": "1Min",
  "lookback_minutes": 45,
  "change_above_percent": "0.50",
  "change_below_percent": "-0.50",
  "min_follow_through_percent": "0.05",
  "max_extension_percent": "1.25",
  "short_average_type": "ema",
  "short_average_window": 9,
  "require_latest_candle_confirmation": true,
  "use_volatility_adjusted_threshold": false,
  "min_momentum_score": "1.50",
  "dedupe_minutes": 120
}
```

## Efficient lookback calculation

If using fixed-minute lookback, map the lookback to a candle index based on timeframe.

Example:

```text
30-minute lookback on 1Min candles -> compare close[-1] to close[-31]
60-minute lookback on 5Min candles -> compare close[-1] to close[-13]
```

Avoid searching timestamps for every strategy row. Precompute timeframe minutes and required candle offsets.

## Percent change formula

```text
percent_change = ((current_close - reference_close) / reference_close) * 100
```

Reject if reference close is missing or zero.

## Bullish signal rules

Core bullish rule:

```text
percent_change >= change_above_percent
```

Recommended confirmations:

```text
latest_close > previous_close
latest_close > latest_open
latest_close > short_ma
```

Optional follow-through filter:

```text
((latest_close - previous_close) / previous_close) * 100 >= min_follow_through_percent
```

## Bearish signal rules

Core bearish rule:

```text
percent_change <= change_below_percent
```

Recommended confirmations:

```text
latest_close < previous_close
latest_close < latest_open
latest_close < short_ma
```

Optional follow-through filter:

```text
((latest_close - previous_close) / previous_close) * 100 <= -min_follow_through_percent
```

## Extension filter

Momentum can enter too late. Reject overextended moves.

```text
extension_percent = abs(latest_close - short_ma) / short_ma * 100
```

Reject when:

```text
extension_percent > max_extension_percent
```

If no short average is used, extension can be measured against VWAP or the lookback start close.

## Volatility-adjusted threshold

A fixed 0.50% move is not equally meaningful for all symbols. Add optional volatility-adjusted scoring.

Calculate average absolute percent change over the recent N candles:

```text
avg_abs_change = average(abs(close[i] - close[i-1]) / close[i-1] * 100)
```

Then:

```text
momentum_score = abs(percent_change) / avg_abs_change
```

Signal only if:

```text
momentum_score >= min_momentum_score
```

This allows the scanner to adapt to symbol volatility.

## One-candle spike rejection

Reject moves that happened mostly in one candle and show no follow-through.

Example:

```text
largest_single_candle_move / abs(total_lookback_move) > 0.80
and latest candle does not confirm direction
```

This avoids buying into a spike that may immediately revert.

## Pullback allowance

A small pullback after a strong move can still be tradable. Configurable behavior:

```json
{
  "allow_small_pullback": true,
  "max_pullback_from_extreme_percent": "0.20"
}
```

Bullish pullback from high:

```text
(highest_close_in_lookback - latest_close) / highest_close_in_lookback * 100
```

Bearish pullback from low:

```text
(latest_close - lowest_close_in_lookback) / lowest_close_in_lookback * 100
```

Reject if pullback is too large.

## Confidence scoring

Suggested scoring:

```text
base = 0.55
+ 0.05 if move exceeds threshold by 25%
+ 0.05 if latest candle confirms direction
+ 0.05 if price is on correct side of short MA
+ 0.05 if market regime confirms
+ 0.05 if volatility-adjusted momentum score is strong
- 0.05 if price is close to extension limit
- 0.05 if move concentrated in one candle
cap at 0.82
```

## Features to persist

```json
{
  "lookback_minutes": 45,
  "timeframe": "1Min",
  "reference_close": "500.10",
  "latest_close": "502.00",
  "percent_change": "0.58",
  "latest_candle_change_percent": "0.08",
  "short_ma": "501.50",
  "extension_percent": "0.10",
  "momentum_score": "1.72",
  "largest_single_candle_share": "0.42"
}
```

## Pseudocode

```python
def evaluate_momentum(config, candles, indicators, market_regime):
    closes = indicators.close
    offset = candles_for_minutes(config.lookback_minutes, config.timeframe)
    if len(closes) <= offset:
        return None

    reference = closes[-1 - offset]
    latest = closes[-1]
    pct = percent_change(latest, reference)

    direction = None
    if pct >= config.change_above_percent:
        direction = "bullish"
    elif pct <= config.change_below_percent:
        direction = "bearish"
    else:
        return None

    if config.require_latest_candle_confirmation and not latest_candle_confirms(direction):
        return None

    if price_is_overextended(direction):
        return None

    if config.use_volatility_adjusted_threshold and momentum_score < config.min_momentum_score:
        return None

    return SignalCandidate(...)
```

## Test cases

- bullish move passes threshold
- bearish move passes threshold
- move below threshold returns no signal
- latest candle reversal rejects signal
- price extension rejects signal
- volatility-adjusted threshold passes/fails
- one-candle spike rejects signal
- not enough candles returns no signal

## Performance notes

This should run extremely fast. It needs only a few list indexes and optional cached EMA. It should be safe to evaluate across a large symbol universe.

## First implementation recommendation

Start with fixed percent thresholds plus latest-candle and extension confirmation. Add volatility-adjusted scoring after baseline paper data exists.

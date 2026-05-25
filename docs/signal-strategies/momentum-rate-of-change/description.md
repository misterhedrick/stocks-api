# Momentum / rate-of-change signal strategy

## Purpose

A momentum strategy attempts to find symbols that are already moving strongly in one direction. The goal is to participate in continuation after a meaningful price move has started.

This is a directional signal strategy. Bullish momentum can map to call entries. Bearish momentum can map to put entries.

## Core idea

Measure price change over a recent lookback window. If price has moved up enough, create a bullish signal. If price has moved down enough, create a bearish signal.

The simplest version uses percent change:

```text
percent_change = ((current_price - previous_price) / previous_price) * 100
```

A more advanced version can also compare the move to recent volatility so that the required move adapts by symbol.

## Suggested default inputs

Intraday starter config:

```json
{
  "timeframe": "1Min",
  "lookback_minutes": 45,
  "change_above_percent": "0.50",
  "change_below_percent": "-0.50",
  "dedupe_minutes": 120
}
```

More conservative config:

```json
{
  "timeframe": "5Min",
  "lookback_minutes": 60,
  "change_above_percent": "0.60",
  "change_below_percent": "-0.60",
  "dedupe_minutes": 360
}
```

## Bullish rules

A bullish signal should require:

1. Current price is above the price from the beginning of the lookback window.
2. Percent change is greater than or equal to `change_above_percent`.
3. Most recent candle is not a sharp reversal candle.
4. Current price is not too extended from a short moving average.
5. Optional volume or quote activity confirms interest.

Example:

```text
percent_change_45m >= 0.50
latest_close >= previous_close
latest_close > short_ma
```

## Bearish rules

A bearish signal should require:

1. Current price is below the price from the beginning of the lookback window.
2. Percent change is less than or equal to `change_below_percent`.
3. Most recent candle is not a sharp bullish reversal candle.
4. Current price is below a short moving average.
5. Optional broad market regime confirms weakness.

Example:

```text
percent_change_45m <= -0.50
latest_close <= previous_close
latest_close < short_ma
```

## Continuation filter

Momentum signals can be dangerous if they trigger after a move is already exhausted. Add continuation checks:

- Last candle closes in the same direction as the signal.
- Pullback from local high or low is not too large.
- Price is not more than a maximum distance from short moving average.
- Avoid entries after very large candles unless a follow-through candle confirms.

Example late-entry block:

```text
abs(current_price - short_ma) / short_ma * 100 > max_extension_percent
```

## Volatility-adjusted version

A stronger implementation compares price change to recent average movement.

Example:

```text
momentum_score = abs(percent_change) / average_absolute_percent_change
```

Signal only if:

```text
momentum_score >= 1.5
```

This helps avoid treating a 0.50% move the same across all symbols. A 0.50% move in a slow ticker may be meaningful, while a 0.50% move in a highly volatile ticker may be normal noise.

## Market regime filter

Momentum works better when the broader market supports the same direction.

Bullish example:

```text
symbol_percent_change > threshold
SPY_percent_change >= 0
QQQ_percent_change >= 0
```

Bearish example:

```text
symbol_percent_change < negative_threshold
SPY_percent_change <= 0
QQQ_percent_change <= 0
```

The strategy can still allow isolated strong moves, but confidence should be lower when market regime disagrees.

## Avoiding bad signals

Skip the signal when:

- Move happened in one candle and immediately reversed.
- Bid/ask quote is stale or underlying data is incomplete.
- Price is too extended from moving average.
- Signal is near major scheduled news or earnings.
- Duplicate signal exists for same symbol, strategy type, and direction.
- Broad market is choppy and directionless.

## Confidence scoring

Possible scoring model:

```text
base = 0.55
+ 0.05 if percent_change exceeds threshold by 25%
+ 0.05 if latest candle confirms direction
+ 0.05 if price is above/below short moving average in signal direction
+ 0.05 if market regime confirms
- 0.05 if price is extended from short moving average
cap at 0.80
```

## Signal output

Bullish example:

```json
{
  "strategy_type": "momentum_rate_of_change",
  "signal_type": "momentum_breakout",
  "direction": "bullish",
  "confidence": "0.62",
  "rationale": "Symbol rose 0.42% over 30 minutes with positive follow-through"
}
```

Bearish example:

```json
{
  "strategy_type": "momentum_rate_of_change",
  "signal_type": "momentum_breakdown",
  "direction": "bearish",
  "confidence": "0.62",
  "rationale": "Symbol fell 0.48% over 30 minutes with bearish follow-through"
}
```

## Best use case

Momentum is best during active trending sessions with strong participation. It can produce poor entries in choppy sessions because moves often reverse quickly. It should be paired with extension limits, market regime filters, and duplicate suppression.

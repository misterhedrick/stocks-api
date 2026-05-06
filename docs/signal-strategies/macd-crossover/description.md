# MACD crossover signal strategy

## Purpose

A MACD strategy uses the Moving Average Convergence Divergence indicator to identify changes in momentum and trend direction. MACD is commonly used for crossover signals, trend confirmation, and momentum acceleration.

This is a directional signal strategy. Bullish MACD signals can map to calls. Bearish MACD signals can map to puts.

## Core idea

MACD is usually built from three values:

- **MACD line**: fast EMA minus slow EMA.
- **Signal line**: EMA of the MACD line.
- **Histogram**: MACD line minus signal line.

Typical default periods are:

```json
{
  "fast_period": 12,
  "slow_period": 26,
  "signal_period": 9
}
```

A bullish signal occurs when the MACD line crosses above the signal line. A bearish signal occurs when the MACD line crosses below the signal line.

## Suggested default inputs

Starter intraday config:

```json
{
  "timeframe": "5Min",
  "lookback_minutes": 2880,
  "fast_period": 12,
  "slow_period": 26,
  "signal_period": 9,
  "require_histogram_confirmation": true,
  "dedupe_minutes": 240
}
```

Faster but noisier config:

```json
{
  "timeframe": "3Min",
  "lookback_minutes": 1440,
  "fast_period": 8,
  "slow_period": 21,
  "signal_period": 5,
  "require_histogram_confirmation": true,
  "dedupe_minutes": 180
}
```

## Bullish rules

A bullish MACD signal should require:

1. Previous MACD line was less than or equal to previous signal line.
2. Current MACD line is above current signal line.
3. Histogram turns positive or increases meaningfully.
4. Price confirms with a higher close or close above a short moving average.
5. Optional market regime is neutral or bullish.

Example:

```text
previous_macd <= previous_signal
current_macd > current_signal
current_histogram > 0
latest_close > previous_close
```

## Bearish rules

A bearish MACD signal should require:

1. Previous MACD line was greater than or equal to previous signal line.
2. Current MACD line is below current signal line.
3. Histogram turns negative or decreases meaningfully.
4. Price confirms with a lower close or close below a short moving average.
5. Optional market regime is neutral or bearish.

Example:

```text
previous_macd >= previous_signal
current_macd < current_signal
current_histogram < 0
latest_close < previous_close
```

## Zero-line filter

The zero line can be used to identify trend strength.

Bullish signals are stronger when:

```text
current_macd > 0
```

Bearish signals are stronger when:

```text
current_macd < 0
```

However, requiring zero-line confirmation can make signals late. A practical implementation can use it as a confidence boost rather than a hard requirement.

## Histogram acceleration filter

The histogram shows the distance between MACD and signal line. For stronger continuation signals, require histogram expansion.

Bullish:

```text
current_histogram > previous_histogram
```

Bearish:

```text
current_histogram < previous_histogram
```

This reduces weak crossovers where MACD barely crosses and immediately reverses.

## Avoiding bad signals

Skip the signal when:

- MACD and signal line are nearly identical and choppy.
- Price does not confirm the crossover.
- Histogram is flat or very small.
- Broader market strongly conflicts with the signal.
- Signal occurs immediately after an unusually large candle.
- A same-symbol same-direction MACD signal was created recently.

## Confidence scoring

Possible scoring model:

```text
base = 0.55
+ 0.05 if histogram confirms direction
+ 0.05 if price confirms direction
+ 0.05 if MACD is on the favorable side of zero line
+ 0.05 if market regime confirms
- 0.05 if histogram magnitude is weak
cap at 0.80
```

## Signal output

Bullish example:

```json
{
  "strategy_type": "macd_crossover",
  "signal_type": "macd_bullish_cross",
  "direction": "bullish",
  "confidence": "0.66",
  "rationale": "MACD crossed above signal line with positive histogram and price confirmation"
}
```

Bearish example:

```json
{
  "strategy_type": "macd_crossover",
  "signal_type": "macd_bearish_cross",
  "direction": "bearish",
  "confidence": "0.66",
  "rationale": "MACD crossed below signal line with negative histogram and price confirmation"
}
```

## Best use case

MACD is best for identifying momentum shifts after price has started turning. It can lag in fast intraday moves, so it should be combined with price confirmation and duplicate suppression. It is usually better as a confirmation signal than as the only reason to enter.

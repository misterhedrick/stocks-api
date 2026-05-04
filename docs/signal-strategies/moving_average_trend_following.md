# Moving average / trend-following signal strategy

## Purpose

A moving average strategy attempts to identify trend direction by smoothing recent price data. The basic assumption is that when short-term average price is above longer-term average price, buyers are currently in control. When short-term average price is below longer-term average price, sellers are currently in control.

This is a directional signal strategy. It should create bullish signals for possible call entries and bearish signals for possible put entries.

## Core idea

Use two moving averages:

- **Short moving average**: reacts faster to recent price changes.
- **Long moving average**: reacts slower and represents the broader trend.

A bullish setup occurs when the short moving average is above the long moving average and price confirms the move. A bearish setup occurs when the short moving average is below the long moving average and price confirms weakness.

## Common moving average types

### Simple moving average

A simple moving average uses the average close over the last N candles. It is easy to reason about but reacts slower.

### Exponential moving average

An exponential moving average gives more weight to recent candles. It reacts faster and is often better for intraday option signals, but it can also produce more false signals.

## Suggested default inputs

For intraday paper testing:

```json
{
  "timeframe": "5Min",
  "lookback_minutes": 1440,
  "short_window": 5,
  "long_window": 20,
  "average_type": "ema",
  "dedupe_minutes": 240
}
```

Alternative slower version:

```json
{
  "timeframe": "15Min",
  "lookback_minutes": 2880,
  "short_window": 8,
  "long_window": 21,
  "average_type": "ema",
  "dedupe_minutes": 360
}
```

## Bullish rules

A bullish signal should require most or all of the following:

1. Short moving average is above long moving average.
2. Current price is above short moving average.
3. Short moving average slope is positive.
4. Long moving average is flat or rising.
5. Recent price change is positive by at least a configured minimum.
6. Optional market regime confirms bullish conditions using broad symbols like SPY and QQQ.

Example bullish trigger:

```text
short_ma > long_ma
current_price > short_ma
short_ma_slope > 0
recent_percent_change >= 0.10
```

## Bearish rules

A bearish signal should require most or all of the following:

1. Short moving average is below long moving average.
2. Current price is below short moving average.
3. Short moving average slope is negative.
4. Long moving average is flat or falling.
5. Recent price change is negative by at least a configured minimum.
6. Optional market regime confirms bearish conditions.

Example bearish trigger:

```text
short_ma < long_ma
current_price < short_ma
short_ma_slope < 0
recent_percent_change <= -0.10
```

## Crossover vs trend state

There are two major variants.

### Crossover

A crossover signal fires only when the short average crosses through the long average. This produces fewer signals and usually catches trend changes earlier.

Bullish crossover:

```text
previous_short_ma <= previous_long_ma
current_short_ma > current_long_ma
```

Bearish crossover:

```text
previous_short_ma >= previous_long_ma
current_short_ma < current_long_ma
```

### Trend state

A trend-state signal fires when the short average is already above or below the long average and price continues to confirm. This produces more signals and is useful for scanning a broader universe.

Bullish trend state:

```text
current_short_ma > current_long_ma
current_price > current_short_ma
```

Bearish trend state:

```text
current_short_ma < current_long_ma
current_price < current_short_ma
```

## Confirmation filters

To reduce false positives, require at least one confirmation filter.

Recommended filters:

- Minimum percent change over lookback window.
- Minimum short moving average slope.
- Price must close beyond the short moving average, not just touch it intrabar.
- Broad market regime must not conflict with the signal.
- Avoid signals during the first 5-15 minutes after market open.
- Avoid new entries too close to market close.

## Avoiding bad signals

Skip the signal when:

- The short and long moving averages are nearly equal and price is chopping sideways.
- Spread between averages is below a minimum threshold.
- Candle data is stale or incomplete.
- Price has already moved too far from the moving average, creating a late entry.
- The same symbol and direction already signaled recently.

## Confidence scoring

Possible confidence inputs:

- Distance between short and long moving averages.
- Slope strength of the short moving average.
- Price distance above or below short moving average.
- Agreement between symbol trend and broad market trend.
- Recent candle consistency.

Example scoring model:

```text
base = 0.55
+ 0.05 if short_ma slope confirms direction
+ 0.05 if price closes beyond short_ma
+ 0.05 if market regime confirms
+ 0.05 if average separation exceeds minimum
cap at 0.80
```

## Signal output

Bullish output example:

```json
{
  "strategy_type": "moving_average",
  "signal_type": "moving_average_setup",
  "direction": "bullish",
  "confidence": "0.65",
  "rationale": "5 EMA is above 20 EMA, price closed above 5 EMA, and EMA slope is positive"
}
```

Bearish output example:

```json
{
  "strategy_type": "moving_average",
  "signal_type": "moving_average_setup",
  "direction": "bearish",
  "confidence": "0.65",
  "rationale": "5 EMA is below 20 EMA, price closed below 5 EMA, and EMA slope is negative"
}
```

## Best use case

This strategy is best for trending markets. It tends to perform poorly in sideways, choppy markets. It should be paired with a market regime filter, volatility filter, or trend-strength filter before it is trusted for larger sizing.

# RSI overbought / oversold implementation deep dive

## Implementation objective

Build an RSI evaluator that can operate in two modes:

1. **Reversal mode**: signal after RSI reaches an extreme and crosses back toward normal.
2. **Continuation mode**: use RSI as trend confirmation when RSI is strong but not extreme.

The first implementation should focus on reversal mode because it is easier to define and test.

## Required data

Minimum candle fields:

```text
timestamp, close, open/high/low optional for candle confirmation
```

Required indicators:

```text
RSI(period)
short EMA optional
trend slope optional
market regime optional
```

## RSI calculation details

Use Wilder's RSI formula for standard behavior.

For each candle:

```text
change = close[i] - close[i-1]
gain = max(change, 0)
loss = abs(min(change, 0))
```

Initial average:

```text
avg_gain = average(gains over first period)
avg_loss = average(losses over first period)
```

Wilder smoothing:

```text
avg_gain = (previous_avg_gain * (period - 1) + current_gain) / period
avg_loss = (previous_avg_loss * (period - 1) + current_loss) / period
```

RSI:

```text
rs = avg_gain / avg_loss
rsi = 100 - (100 / (1 + rs))
```

Edge cases:

```text
avg_loss == 0 and avg_gain > 0 -> RSI = 100
avg_gain == 0 and avg_loss > 0 -> RSI = 0
avg_gain == 0 and avg_loss == 0 -> RSI = 50 or None; choose 50 for flat data
```

## Config schema proposal

```json
{
  "type": "rsi_reversal",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "rsi_period": 14,
  "oversold_level": 30,
  "overbought_level": 70,
  "confirmation_mode": "cross_back_inside",
  "require_price_confirmation": true,
  "require_trend_safety_filter": true,
  "trend_average_type": "ema",
  "trend_average_window": 20,
  "max_opposing_trend_slope_percent": "0.20",
  "dedupe_minutes": 240
}
```

## Bullish reversal logic

The safest bullish RSI signal happens when RSI moves out of oversold conditions.

```text
previous_rsi < oversold_level
current_rsi >= oversold_level
```

Require price confirmation:

```text
latest_close > previous_close
```

Optional candle confirmation:

```text
latest_close > latest_open
close_position_in_range >= 0.60
```

## Bearish reversal logic

The safest bearish RSI signal happens when RSI moves down out of overbought conditions.

```text
previous_rsi > overbought_level
current_rsi <= overbought_level
```

Require price confirmation:

```text
latest_close < previous_close
```

Optional candle confirmation:

```text
latest_close < latest_open
close_position_in_range <= 0.40
```

## Alternative confirmation modes

### cross_back_inside

Most conservative and recommended for first implementation.

### extreme_plus_reversal_candle

Allows signal while RSI is still extreme, but only if a strong reversal candle appears.

Bullish:

```text
current_rsi < oversold_level
latest_close > latest_open
latest_close > previous_close
```

Bearish:

```text
current_rsi > overbought_level
latest_close < latest_open
latest_close < previous_close
```

### continuation

RSI supports trend rather than reversal.

Bullish:

```text
50 <= rsi <= 70
price > trend_ma
trend_ma_slope > 0
```

Bearish:

```text
30 <= rsi <= 50
price < trend_ma
trend_ma_slope < 0
```

This should be a separate strategy type later, not mixed into the first reversal evaluator.

## Trend safety filter

RSI extremes can persist during strong trends. Add a slope filter.

Bullish reversal should reject when:

```text
trend_ma_slope_percent < -max_opposing_trend_slope_percent
```

Bearish reversal should reject when:

```text
trend_ma_slope_percent > max_opposing_trend_slope_percent
```

## Divergence extension

Do not implement divergence first, but design feature storage so it can be added.

Bullish divergence:

```text
price lower low
RSI higher low
```

Bearish divergence:

```text
price higher high
RSI lower high
```

This requires swing detection. It should reuse support/resistance swing utilities.

## Features to persist

```json
{
  "rsi_period": 14,
  "previous_rsi": "28.4",
  "current_rsi": "31.2",
  "oversold_level": 30,
  "overbought_level": 70,
  "confirmation_mode": "cross_back_inside",
  "price_confirmation": true,
  "trend_ma_slope_percent": "-0.03"
}
```

## Confidence scoring

```text
base = 0.50
+ 0.05 if RSI crossed back inside threshold
+ 0.05 if price candle confirms direction
+ 0.05 if trend safety filter passes cleanly
+ 0.05 if market regime does not conflict
+ 0.05 if RSI reversal is sharp enough
- 0.05 if RSI barely crossed threshold
- 0.10 if trend slope is close to rejection threshold
cap at 0.75
```

## Pseudocode

```python
def evaluate_rsi_reversal(config, candles, indicators, market_regime):
    rsi = indicators.rsi(config.rsi_period)
    if rsi[-1] is None or rsi[-2] is None:
        return None

    bullish = rsi[-2] < config.oversold_level and rsi[-1] >= config.oversold_level
    bearish = rsi[-2] > config.overbought_level and rsi[-1] <= config.overbought_level

    if not bullish and not bearish:
        return None

    direction = "bullish" if bullish else "bearish"

    if config.require_price_confirmation and not price_confirms(direction):
        return None

    if trend_safety_rejects(direction):
        return None

    return SignalCandidate(...)
```

## Tests

- bullish RSI cross above oversold creates signal
- bearish RSI cross below overbought creates signal
- RSI extreme but no cross returns no signal in conservative mode
- price confirmation failure returns no signal
- strong opposing trend rejects signal
- flat RSI data handles division-by-zero safely
- not enough RSI warmup returns no signal

## First implementation recommendation

Implement only `cross_back_inside` reversal mode first. Add continuation and divergence later as separate profiles.

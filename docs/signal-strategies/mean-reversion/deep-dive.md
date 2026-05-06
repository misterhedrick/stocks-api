# Mean reversion implementation deep dive

## Implementation objective

Build a contrarian evaluator that detects when price has stretched too far from a reference mean and then shows evidence of reversing back toward that mean.

Mean reversion must be implemented more conservatively than trend-following because it often trades against the most recent move.

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume optional
```

Useful indicators:

```text
VWAP if available
EMA or SMA mean
Bollinger Bands
RSI optional
ATR optional
trend slope optional
market regime optional
```

## Config schema proposal

```json
{
  "type": "mean_reversion",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "mean_source": "ema",
  "mean_window": 20,
  "min_distance_percent": "0.75",
  "confirmation_mode": "close_back_toward_mean",
  "require_reversal_candle": true,
  "max_trend_slope_percent": "0.20",
  "use_rsi_filter": true,
  "rsi_period": 14,
  "oversold_level": 35,
  "overbought_level": 65,
  "dedupe_minutes": 240
}
```

## Mean source choices

### EMA/SMA mean

Simplest implementation:

```text
mean = EMA(close, mean_window)
```

This requires only close prices and works with existing candle data.

### VWAP mean

Best intraday mean if reliable volume data exists:

```text
vwap = cumulative(price * volume) / cumulative(volume)
```

Use typical price if available:

```text
typical_price = (high + low + close) / 3
```

If volume is missing or unreliable, do not use VWAP.

### Bollinger middle band

Bollinger middle band is usually an SMA. It pairs naturally with upper/lower band reversion.

## Bullish setup

Bullish mean reversion means price is below the mean and starts moving back up.

Core stretch:

```text
latest_close < mean * (1 - min_distance_percent / 100)
```

Confirmation options:

```text
latest_close > previous_close
latest_close > latest_open
latest_close > latest_low + 50% of candle range
```

Stronger Bollinger confirmation:

```text
latest_low < lower_band
latest_close > lower_band
```

## Bearish setup

Bearish mean reversion means price is above the mean and starts moving back down.

Core stretch:

```text
latest_close > mean * (1 + min_distance_percent / 100)
```

Confirmation options:

```text
latest_close < previous_close
latest_close < latest_open
latest_close < latest_high - 50% of candle range
```

Stronger Bollinger confirmation:

```text
latest_high > upper_band
latest_close < upper_band
```

## Trend safety filter

Do not fade strong trends.

Calculate slope of the mean:

```text
mean_slope_percent = ((mean[-1] - mean[-1 - slope_window]) / mean[-1 - slope_window]) * 100
```

For bullish reversion, reject if mean slope is strongly negative:

```text
mean_slope_percent < -max_trend_slope_percent
```

For bearish reversion, reject if mean slope is strongly positive:

```text
mean_slope_percent > max_trend_slope_percent
```

This prevents buying puts/calls against a persistent trend too early.

## RSI filter

RSI can confirm exhaustion.

Bullish:

```text
rsi <= oversold_level
or previous_rsi < oversold_level and current_rsi > previous_rsi
```

Bearish:

```text
rsi >= overbought_level
or previous_rsi > overbought_level and current_rsi < previous_rsi
```

Use RSI as a confidence boost first. Make it a hard requirement only after tests show it improves selectivity.

## Target reference

Mean reversion signals should persist the mean as an expected target reference.

```json
{
  "mean_price": "501.22",
  "latest_close": "497.30",
  "distance_from_mean_percent": "0.78",
  "expected_reversion_target": "501.22"
}
```

This will help later AI trade review measure whether the trade moved toward the expected mean.

## Reversal candle quality

Bullish candle quality:

```text
close > open
close_position_in_range >= 0.60
lower_wick exists or close > previous_close
```

Bearish candle quality:

```text
close < open
close_position_in_range <= 0.40
upper_wick exists or close < previous_close
```

## Skip rules

Reject when:

```text
not enough indicator warmup
mean is missing
price is not far enough from mean
reversal candle not confirmed
trend slope is too strong against reversal
market regime strongly opposes signal
latest candle is extremely large news candle
spread/liquidity later fails contract selection
```

## Confidence scoring

```text
base = 0.50
+ 0.05 if distance from mean exceeds threshold by 25%
+ 0.05 if candle closes back toward mean
+ 0.05 if RSI confirms exhaustion
+ 0.05 if Bollinger band rejection confirms
+ 0.05 if market regime does not conflict
- 0.10 if mean slope is close to rejection threshold
cap at 0.75
```

Mean reversion should have a lower confidence cap than trend-following until trade data proves reliability.

## Features to persist

```json
{
  "mean_source": "ema",
  "mean_window": 20,
  "mean_price": "501.22",
  "latest_close": "497.30",
  "distance_from_mean_percent": "0.78",
  "mean_slope_percent": "-0.04",
  "rsi": "31.2",
  "reversal_candle": true,
  "close_position_in_range": "0.67"
}
```

## Pseudocode

```python
def evaluate_mean_reversion(config, candles, indicators, market_regime):
    mean = resolve_mean(config, indicators)
    if mean[-1] is None:
        return None

    latest = candles[-1]
    distance_pct = percent_distance(latest.close, mean[-1])

    if distance_pct <= -config.min_distance_percent:
        direction = "bullish"
    elif distance_pct >= config.min_distance_percent:
        direction = "bearish"
    else:
        return None

    if not reversal_confirms(direction, latest, candles[-2]):
        return None

    if trend_slope_rejects(direction, mean):
        return None

    confidence = score(...)
    return SignalCandidate(...)
```

## Tests

- bullish stretch with reversal creates signal
- bearish stretch with reversal creates signal
- stretch without reversal returns no signal
- strong trend rejects reversal
- RSI filter boosts/confirms
- Bollinger rejection confirms
- missing volume rejects VWAP mode
- not enough candles returns no signal

## First implementation recommendation

Start with EMA mean reversion. Add VWAP after confirming candle volume data quality. Add Bollinger and RSI as optional confirmation filters after shared indicators exist.

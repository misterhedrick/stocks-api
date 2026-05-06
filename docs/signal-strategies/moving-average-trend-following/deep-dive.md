# Moving average / trend-following implementation deep dive

## Implementation objective

Implement a fast directional evaluator that detects trend alignment, trend continuation, or moving-average crossovers using cached candle and indicator data.

This should replace ad hoc moving-average scanner logic with a normalized evaluator that can be reused across all symbols and configured strategy rows.

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume optional
```

Required indicators:

```text
short SMA or EMA
long SMA or EMA
short average slope
long average slope optional
recent percent change optional
market regime optional
```

Recommended default timeframe:

```text
5Min
```

Recommended minimum candles:

```text
max(short_window, long_window) + slope_window + 2
```

For a 5/20 EMA setup with 3-candle slope, fetch at least 25 completed candles. In production, use more warmup than the exact minimum so EMA stabilizes.

## Config schema proposal

```json
{
  "type": "moving_average",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "lookback_minutes": 1440,
  "average_type": "ema",
  "short_window": 5,
  "long_window": 20,
  "trigger": "bullish_trend",
  "direction": "bullish",
  "min_change_percent": "0.10",
  "min_average_separation_percent": "0.02",
  "slope_window": 3,
  "require_short_average_slope": true,
  "require_price_confirmation": true,
  "require_long_average_slope": false,
  "max_extension_percent": "1.00",
  "dedupe_minutes": 240
}
```

## Trigger variants

### bullish_cross

Use when the short average has just crossed above the long average.

```text
previous_short_ma <= previous_long_ma
current_short_ma > current_long_ma
```

This catches a trend change but can create false positives in chop.

### bearish_cross

```text
previous_short_ma >= previous_long_ma
current_short_ma < current_long_ma
```

### bullish_trend

Use when the trend is already established.

```text
current_short_ma > current_long_ma
current_close > current_short_ma
```

This generates more signals than crossover mode.

### bearish_trend

```text
current_short_ma < current_long_ma
current_close < current_short_ma
```

## Efficient calculation details

Compute both moving-average series once per symbol/timeframe:

```python
short_ma = indicators.ema(short_window)
long_ma = indicators.ema(long_window)
```

Use the last completed candle only:

```python
idx = -1
prev_idx = -2
```

Do not use the currently forming candle unless the strategy explicitly allows intrabar evaluation.

## Slope calculation

Use percent slope across a configurable window:

```text
slope_percent = ((ma[-1] - ma[-1 - slope_window]) / ma[-1 - slope_window]) * 100
```

Bullish slope passes when:

```text
short_slope_percent > 0
```

Bearish slope passes when:

```text
short_slope_percent < 0
```

For stricter trend confirmation:

```text
long_slope_percent >= 0 for bullish
long_slope_percent <= 0 for bearish
```

## Average separation filter

When short and long averages are nearly equal, signals are more likely to be chop.

Calculate:

```text
separation_percent = abs(short_ma - long_ma) / long_ma * 100
```

Require:

```text
separation_percent >= min_average_separation_percent
```

This should be small for liquid large caps, for example `0.02` to `0.10` percent depending on timeframe.

## Price confirmation

Bullish price confirmation:

```text
latest_close > short_ma
```

Bearish price confirmation:

```text
latest_close < short_ma
```

Optional stricter confirmation:

```text
bullish: latest_close > latest_open
bearish: latest_close < latest_open
```

## Extension filter

Avoid entering after price has moved too far away from the short average.

```text
extension_percent = abs(latest_close - short_ma) / short_ma * 100
```

Reject when:

```text
extension_percent > max_extension_percent
```

This prevents late entries into candles that are already overextended.

## Market regime integration

Market regime can be a confidence boost or hard filter.

Bullish signal should prefer:

```text
SPY and/or QQQ short-term change >= 0
```

Bearish signal should prefer:

```text
SPY and/or QQQ short-term change <= 0
```

For single-stock signals, do not always hard-block when market disagrees; instead reduce confidence unless the strategy config says `market_regime.required=true`.

## Signal candidate features

Persist useful features for later trade review:

```json
{
  "short_ma": "502.14",
  "long_ma": "501.30",
  "short_window": 5,
  "long_window": 20,
  "average_type": "ema",
  "short_slope_percent": "0.06",
  "long_slope_percent": "0.02",
  "average_separation_percent": "0.17",
  "price_extension_percent": "0.22",
  "latest_close": "503.20",
  "trigger": "bullish_trend"
}
```

## Pseudocode

```python
def evaluate_moving_average(config, candles, indicators, market_regime):
    closes = indicators.close
    short_ma = indicators.average(config.average_type, config.short_window)
    long_ma = indicators.average(config.average_type, config.long_window)

    if short_ma[-1] is None or long_ma[-1] is None:
        return None

    latest_close = closes[-1]
    previous_short = short_ma[-2]
    previous_long = long_ma[-2]
    current_short = short_ma[-1]
    current_long = long_ma[-1]

    if not trigger_passes(config.trigger, previous_short, previous_long, current_short, current_long, latest_close):
        return None

    if config.require_short_average_slope and not slope_confirms(...):
        return None

    if config.require_price_confirmation and not price_confirms(...):
        return None

    if average_separation_too_small(...):
        return None

    if price_too_extended(...):
        return None

    confidence = score(...)
    return SignalCandidate(...)
```

## Test cases

Add unit tests for:

- bullish crossover creates bullish signal
- bearish crossover creates bearish signal
- bullish trend creates signal without fresh crossover
- bearish trend creates signal without fresh crossover
- short and long averages equal -> no signal
- price confirmation fails -> no signal
- slope requirement fails -> no signal
- price extension too large -> no signal
- not enough candles -> no signal
- stale candles -> no signal

## Performance notes

For each symbol/timeframe, cache:

```text
EMA(5), EMA(8), EMA(20), EMA(21)
```

Do not recalculate EMA per strategy row. Use the indicator engine requirement planner.

## First production-safe implementation

Start with:

```text
trigger: bullish_trend / bearish_trend
average_type: ema
short_window: 5
long_window: 20
require_price_confirmation: true
require_short_average_slope: true
min_average_separation_percent: 0.02
max_extension_percent: 1.00
```

This should generate more stable signals than raw crossovers while still being simple to test.

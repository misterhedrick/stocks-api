# Breakout / price-threshold implementation deep dive

## Implementation objective

Build a reusable breakout evaluator that can support fixed thresholds, recent range highs/lows, previous-day levels, premarket levels, and opening-range levels without duplicating logic.

The evaluator should separate two concerns:

```text
level calculation -> produces breakout/breakdown levels
signal evaluation -> checks whether price crossed those levels with confirmation
```

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume optional
```

Required derived values:

```text
latest close
previous close
latest high/low
range high/range low
breakout level/breakdown level
optional relative volume
optional market regime
```

## Config schema proposal

```json
{
  "type": "breakout_price_threshold",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "level_source": "recent_range",
  "lookback_candles": 20,
  "price_above": null,
  "price_below": null,
  "breakout_buffer_percent": "0.05",
  "min_confirming_closes": 1,
  "require_candle_body_confirmation": true,
  "max_extension_percent": "1.25",
  "dedupe_minutes": 240
}
```

## Level sources

Implement level source as a small resolver function.

```python
def resolve_breakout_levels(config, candles) -> BreakoutLevels:
    ...
```

Supported sources:

```text
fixed_threshold
recent_range
opening_range
previous_day
premarket
manual_support_resistance
```

## Fixed threshold implementation

Use explicitly configured `price_above` and/or `price_below`.

Bullish trigger:

```text
previous_close <= price_above
latest_close > price_above * (1 + buffer)
```

Bearish trigger:

```text
previous_close >= price_below
latest_close < price_below * (1 - buffer)
```

This is the simplest path and matches the current price-threshold strategy.

## Recent range implementation

Calculate the highest high and lowest low over the last N completed candles, excluding the latest candle if it is the breakout candle.

```text
range_high = max(high[-lookback_candles-1:-1])
range_low = min(low[-lookback_candles-1:-1])
```

Bullish:

```text
latest_close > range_high * (1 + buffer)
```

Bearish:

```text
latest_close < range_low * (1 - buffer)
```

## Opening range implementation

For the first version, calculate opening range from intraday candles:

```text
range start = 9:30am America/New_York
range end = 10:00am America/New_York for 30-minute opening range
```

Do not evaluate until range end has passed.

Required checks:

```text
current_time > opening_range_end
opening_range has at least expected candle count
latest candle timestamp > opening_range_end
```

Bullish:

```text
latest_close > opening_range_high * (1 + buffer)
```

Bearish:

```text
latest_close < opening_range_low * (1 - buffer)
```

## Confirmation closes

`min_confirming_closes` controls how many consecutive closes must be beyond the level.

Bullish:

```text
all(close > breakout_level * (1 + buffer) for close in last_n_closes)
```

Bearish:

```text
all(close < breakdown_level * (1 - buffer) for close in last_n_closes)
```

One confirming close is more responsive. Two confirming closes reduce false breakouts.

## Candle body confirmation

Avoid signals where only a wick breaks the level.

Bullish body confirmation:

```text
latest_close > breakout_level
latest_close > latest_open
```

Bearish body confirmation:

```text
latest_close < breakdown_level
latest_close < latest_open
```

Optional close-position filter:

```text
close_position = (close - low) / (high - low)
```

Bullish:

```text
close_position >= 0.60
```

Bearish:

```text
close_position <= 0.40
```

## Failed breakout rejection

Reject if latest close returns inside the prior range.

Bullish rejection:

```text
latest_high > breakout_level
latest_close <= breakout_level
```

Bearish rejection:

```text
latest_low < breakdown_level
latest_close >= breakdown_level
```

These are not entry signals; they may later become support/resistance rejection signals.

## Extension filter

Avoid chasing a breakout that is too far from the breakout level.

```text
extension_percent = abs(latest_close - breakout_level) / breakout_level * 100
```

Reject when:

```text
extension_percent > max_extension_percent
```

## Features to persist

```json
{
  "level_source": "recent_range",
  "breakout_level": "502.25",
  "breakdown_level": "498.10",
  "latest_close": "503.00",
  "breakout_buffer_percent": "0.05",
  "extension_percent": "0.15",
  "confirming_closes": 1,
  "close_position_in_range": "0.78"
}
```

## Confidence scoring

```text
base = 0.56
+ 0.05 if close confirms beyond level
+ 0.05 if candle body confirms direction
+ 0.05 if multiple confirming closes
+ 0.05 if volume confirms
+ 0.05 if market regime confirms
- 0.05 if extension is near max limit
cap at 0.82
```

## Pseudocode

```python
def evaluate_breakout(config, candles, indicators, market_regime):
    levels = resolve_breakout_levels(config, candles)
    if not levels.ready:
        return None

    latest = candles[-1]
    previous = candles[-2]

    bullish = previous.close <= levels.breakout and latest.close > buffered_up(levels.breakout)
    bearish = previous.close >= levels.breakdown and latest.close < buffered_down(levels.breakdown)

    if not bullish and not bearish:
        return None

    direction = "bullish" if bullish else "bearish"
    level = levels.breakout if bullish else levels.breakdown

    if not confirmation_closes_pass(direction, level):
        return None
    if config.require_candle_body_confirmation and not body_confirms(direction, latest):
        return None
    if too_extended(latest.close, level):
        return None

    return SignalCandidate(...)
```

## Tests

- fixed bullish threshold breakout
- fixed bearish threshold breakdown
- recent range bullish breakout
- recent range bearish breakdown
- opening range not ready -> no signal
- wick-only breakout -> no signal
- close back inside range -> no signal
- extension too large -> no signal
- multiple confirming closes required/pass/fail

## First implementation recommendation

Start with `fixed_threshold` and `recent_range`. Add opening-range support after intraday session slicing utilities are solid.

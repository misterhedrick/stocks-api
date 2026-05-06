# Volume-confirmed breakout implementation deep dive

## Implementation objective

Build a breakout evaluator that only creates signals when both price action and relative volume confirm participation. This should be stricter than a plain price-threshold breakout.

The evaluator should reuse breakout level resolution and add volume and candle-quality checks.

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume
```

Volume is required. If volume is missing or zero for too many candles, skip the strategy.

Required derived values:

```text
breakout/breakdown level
latest volume
average volume
relative volume
latest candle body quality
optional market regime
```

## Config schema proposal

```json
{
  "type": "volume_confirmed_breakout",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "level_source": "recent_range",
  "lookback_candles": 20,
  "volume_lookback_candles": 20,
  "relative_volume_min": "1.50",
  "breakout_buffer_percent": "0.05",
  "min_body_percent_of_range": "40",
  "min_close_position_bullish": "0.60",
  "max_close_position_bearish": "0.40",
  "max_extension_percent": "1.25",
  "dedupe_minutes": 240
}
```

## Relative volume calculation

Simple rolling relative volume:

```text
average_volume = average(volume[-volume_lookback_candles-1:-1])
relative_volume = latest_volume / average_volume
```

Exclude the latest candle from the average so the breakout candle does not inflate its own baseline.

Reject if:

```text
average_volume <= 0
latest_volume is None
relative_volume < relative_volume_min
```

## Time-of-day volume issue

Intraday volume is naturally higher near open and close. A simple rolling average is acceptable for the first implementation, but later it should be improved to compare against same-time-of-day historical averages.

Future improvement:

```text
relative_volume_by_time = latest_volume / avg_volume_for_symbol_time_bucket
```

## Bullish rules

```text
latest_close > breakout_level * (1 + breakout_buffer_percent / 100)
relative_volume >= relative_volume_min
latest_close > latest_open
close_position_in_range >= min_close_position_bullish
body_percent_of_range >= min_body_percent_of_range
```

## Bearish rules

```text
latest_close < breakdown_level * (1 - breakout_buffer_percent / 100)
relative_volume >= relative_volume_min
latest_close < latest_open
close_position_in_range <= max_close_position_bearish
body_percent_of_range >= min_body_percent_of_range
```

## Candle quality calculations

Range:

```text
candle_range = high - low
```

Body:

```text
body = abs(close - open)
```

Body percent:

```text
body_percent_of_range = body / candle_range * 100
```

Close position:

```text
close_position = (close - low) / candle_range
```

Reject if candle range is zero.

## Extension filter

Avoid chasing breakouts that are already far beyond the level.

Bullish:

```text
extension_percent = (latest_close - breakout_level) / breakout_level * 100
```

Bearish:

```text
extension_percent = (breakdown_level - latest_close) / breakdown_level * 100
```

Reject when:

```text
extension_percent > max_extension_percent
```

## Level integration

This strategy should reuse the breakout level resolver:

```text
fixed_threshold
recent_range
opening_range
support_resistance level
previous_day level
premarket level
```

Do not duplicate level detection. Keep volume-confirmed breakout as a stricter wrapper around breakout logic.

## Features to persist

```json
{
  "level_source": "recent_range",
  "breakout_level": "502.25",
  "latest_close": "503.10",
  "latest_volume": "300000",
  "average_volume": "160000",
  "relative_volume": "1.88",
  "body_percent_of_range": "62.0",
  "close_position_in_range": "0.82",
  "extension_percent": "0.17"
}
```

## Confidence scoring

```text
base = 0.60
+ 0.05 if relative_volume >= 1.50
+ 0.05 if relative_volume >= 2.00
+ 0.05 if candle body quality is strong
+ 0.05 if level strength is high
+ 0.05 if market regime confirms
- 0.05 if extension is near max limit
cap at 0.87
```

## Pseudocode

```python
def evaluate_volume_breakout(config, candles, indicators, market_regime):
    levels = resolve_breakout_levels(config, candles)
    if not levels.ready:
        return None

    latest = candles[-1]
    rel_volume = calculate_relative_volume(candles, config.volume_lookback_candles)
    if rel_volume is None or rel_volume < config.relative_volume_min:
        return None

    direction = detect_breakout_direction(latest, levels, config.breakout_buffer_percent)
    if direction is None:
        return None

    if not candle_quality_confirms(direction, latest, config):
        return None

    if too_extended(direction, latest.close, levels):
        return None

    return SignalCandidate(...)
```

## Tests

- bullish breakout with high relative volume creates signal
- bearish breakdown with high relative volume creates signal
- price breakout without volume returns no signal
- volume spike without price breakout returns no signal
- wick-only breakout returns no signal
- zero volume baseline returns no signal
- extension too large returns no signal

## First implementation recommendation

Build this after plain breakout logic exists. Reuse the same level resolver and add relative-volume + candle-quality confirmation.

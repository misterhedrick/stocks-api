# Support / resistance implementation deep dive

## Implementation objective

Build a level-based signal evaluator that can identify important price levels, score those levels, then generate either breakout or rejection signals around them.

This strategy is more complex than simple threshold logic because it needs a level-detection layer before signal evaluation.

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume optional
```

Required derived values:

```text
swing highs
swing lows
support zones
resistance zones
level strength
latest candle behavior around level
optional volume confirmation
optional market regime
```

## Config schema proposal

```json
{
  "type": "support_resistance",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "lookback_candles": 60,
  "swing_window": 3,
  "level_tolerance_percent": "0.10",
  "min_touches": 2,
  "mode": "breakout_or_rejection",
  "breakout_buffer_percent": "0.05",
  "require_volume_confirmation": false,
  "relative_volume_min": "1.50",
  "dedupe_minutes": 240
}
```

## Level detection step

Detect local swing highs and swing lows.

Swing high:

```text
high[i] > high[i - 1]
high[i] > high[i - 2]
high[i] > high[i + 1]
high[i] > high[i + 2]
```

Swing low:

```text
low[i] < low[i - 1]
low[i] < low[i - 2]
low[i] < low[i + 1]
low[i] < low[i + 2]
```

The `swing_window` controls how many candles on each side must confirm the swing.

## Zone clustering

Do not treat every swing as its own exact level. Group nearby levels into zones.

Tolerance:

```text
zone_tolerance = level_price * level_tolerance_percent / 100
```

Two swing highs belong to the same resistance zone when:

```text
abs(swing_high_price - zone_center) <= zone_tolerance
```

Two swing lows belong to the same support zone using the same logic.

Zone center can be recalculated as the average of included swing prices.

## Level object

Recommended internal type:

```python
@dataclass(frozen=True, slots=True)
class PriceLevel:
    kind: Literal["support", "resistance"]
    price: float
    low: float
    high: float
    touches: int
    first_seen: datetime
    last_seen: datetime
    strength: float
    source: str
```

For zones:

```text
low = price - tolerance
high = price + tolerance
```

## Level strength scoring

Score each level before using it.

Potential components:

```text
+ touches score
+ recency score
+ volume score near level
+ prior day / premarket confluence bonus
+ strong rejection candle bonus
```

Example:

```text
strength = 0.40
+ min(touches, 4) * 0.08
+ recency_bonus
+ confluence_bonus
+ volume_bonus
cap at 0.90
```

Only use levels with:

```text
touches >= min_touches
strength >= min_strength
```

## Choosing relevant levels

For each symbol, identify nearest support below/near current price and nearest resistance above/near current price.

```text
nearest_support = max(level.price for support where level.price <= latest_close + tolerance)
nearest_resistance = min(level.price for resistance where level.price >= latest_close - tolerance)
```

Avoid scanning all levels after each candle by sorting levels by price.

## Breakout signal mode

### Bullish resistance breakout

```text
previous_close <= resistance.high
latest_close > resistance.high * (1 + breakout_buffer_percent / 100)
```

Recommended confirmation:

```text
latest_close > latest_open
close_position_in_range >= 0.60
```

### Bearish support breakdown

```text
previous_close >= support.low
latest_close < support.low * (1 - breakout_buffer_percent / 100)
```

Recommended confirmation:

```text
latest_close < latest_open
close_position_in_range <= 0.40
```

## Rejection / bounce mode

### Bullish support bounce

```text
latest_low <= support.high
latest_close > support.high
latest_close > latest_open
```

This means price tested support and closed back above the support zone.

### Bearish resistance rejection

```text
latest_high >= resistance.low
latest_close < resistance.low
latest_close < latest_open
```

This means price tested resistance and closed back below the resistance zone.

## Volume integration

Volume is optional for the first implementation but valuable for confidence.

Relative volume:

```text
relative_volume = latest_volume / average_volume(last N candles)
```

Breakout signals get a boost when:

```text
relative_volume >= relative_volume_min
```

Rejection signals can also use volume, but high volume can mean either rejection or continuation. For rejection, candle close behavior is more important than volume alone.

## Avoiding noisy levels

Reject levels when:

```text
level touches are too old
level zone is too wide
price has crossed the level too many times recently
level is too close to current price without a clean test
conflicting support and resistance are too close together
```

A useful concept is `level_chop_count`: number of closes alternating above/below the level in the last N candles. High chop count means ignore the level.

## Features to persist

```json
{
  "level_kind": "resistance",
  "level_price": "502.50",
  "level_low": "502.00",
  "level_high": "503.00",
  "level_touches": 3,
  "level_strength": "0.72",
  "mode": "breakout",
  "latest_close": "503.20",
  "breakout_buffer_percent": "0.05",
  "relative_volume": "1.60",
  "close_position_in_range": "0.76"
}
```

## Confidence scoring

```text
base = 0.55
+ 0.05 if level has >= 2 touches
+ 0.05 if level has >= 3 touches
+ 0.05 if latest candle confirms direction
+ 0.05 if relative volume confirms breakout
+ 0.05 if market regime confirms
+ 0.05 if level aligns with prior-day/premarket level
- 0.05 if level chop count is elevated
cap at 0.85
```

## Pseudocode

```python
def evaluate_support_resistance(config, candles, indicators, market_regime):
    levels = detect_or_load_levels(config, candles)
    relevant = choose_relevant_levels(levels, candles[-1].close)

    for level in relevant:
        signal = evaluate_level_interaction(config, level, candles)
        if signal:
            return signal

    return None
```

## Tests

- detects swing highs/lows
- clusters nearby highs into resistance zone
- clusters nearby lows into support zone
- bullish resistance breakout creates signal
- bearish support breakdown creates signal
- bullish support bounce creates signal
- bearish resistance rejection creates signal
- weak one-touch level ignored
- high chop level ignored
- level too old ignored

## First implementation recommendation

Start with previous day high/low and recent swing high/low levels. Avoid complex multi-day clustering until the simpler version is tested. Implement breakout mode first, then rejection mode.

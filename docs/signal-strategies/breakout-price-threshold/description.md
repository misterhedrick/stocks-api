# Breakout / price-threshold signal strategy

## Purpose

A breakout strategy attempts to identify when price moves beyond a meaningful level. That level can be a fixed price threshold, recent high, recent low, premarket high, previous day high or low, or a manually configured support/resistance level.

This is a directional signal strategy. Bullish breakouts can map to call entries. Bearish breakdowns can map to put entries.

## Core idea

A breakout signal assumes that when price pushes through an important level, new buyers or sellers may enter and cause continuation.

Bullish breakout:

```text
current_price > breakout_level
```

Bearish breakdown:

```text
current_price < breakdown_level
```

The basic version is easy to implement, but it needs confirmation filters to avoid false breakouts.

## Common breakout levels

Useful threshold sources:

- Manually configured price above or below.
- Previous day high.
- Previous day low.
- Premarket high.
- Premarket low.
- Opening range high.
- Opening range low.
- Recent N-candle high.
- Recent N-candle low.
- Resistance/support from pivot logic.

## Suggested default inputs

Opening range breakout example:

```json
{
  "timeframe": "5Min",
  "opening_range_minutes": 30,
  "breakout_buffer_percent": "0.05",
  "min_confirming_closes": 1,
  "dedupe_minutes": 240
}
```

Recent range breakout example:

```json
{
  "timeframe": "5Min",
  "lookback_candles": 20,
  "breakout_buffer_percent": "0.05",
  "min_confirming_closes": 1,
  "dedupe_minutes": 240
}
```

Fixed threshold example:

```json
{
  "price_above": "500.00",
  "price_below": "490.00",
  "dedupe_minutes": 240
}
```

## Bullish rules

A bullish breakout should require:

1. Current price crosses above the configured breakout level.
2. Price exceeds the level by a minimum buffer.
3. Close confirms above the level, not only an intrabar wick.
4. Optional volume confirms participation.
5. Optional market regime is neutral or bullish.

Example:

```text
latest_close > breakout_level * 1.0005
previous_close <= breakout_level
```

## Bearish rules

A bearish breakdown should require:

1. Current price crosses below the configured breakdown level.
2. Price falls below the level by a minimum buffer.
3. Close confirms below the level.
4. Optional volume confirms participation.
5. Optional market regime is neutral or bearish.

Example:

```text
latest_close < breakdown_level * 0.9995
previous_close >= breakdown_level
```

## Confirmation filters

Breakouts often fail. Confirmation filters are important.

Recommended filters:

- Require one or more candle closes beyond the level.
- Require price to hold beyond the level for N minutes.
- Require volume above recent average.
- Require spread between current price and threshold to exceed a small buffer.
- Reject if breakout candle is too large and price is extended.
- Reject if price immediately falls back inside the prior range.

## False breakout detection

A false breakout happens when price crosses the level but quickly reverses.

Possible rejection rules:

```text
bullish_signal and latest_close < breakout_level -> reject
bearish_signal and latest_close > breakdown_level -> reject
```

If using candle wicks:

```text
bullish breakout requires candle close above level, not only high above level
bearish breakdown requires candle close below level, not only low below level
```

## Opening range variant

The opening range variant waits for the first N minutes after market open and records the high/low.

Example:

```text
opening_range_high = max(highs from 9:30 to 10:00)
opening_range_low = min(lows from 9:30 to 10:00)
```

Bullish:

```text
latest_close > opening_range_high
```

Bearish:

```text
latest_close < opening_range_low
```

This strategy should not fire until the opening range window is complete.

## Confidence scoring

Possible scoring model:

```text
base = 0.55
+ 0.05 if close confirms beyond level
+ 0.05 if volume is above average
+ 0.05 if broad market confirms direction
+ 0.05 if breakout buffer is cleanly exceeded
- 0.05 if candle is overextended
cap at 0.80
```

## Signal output

Bullish example:

```json
{
  "strategy_type": "breakout_price_threshold",
  "signal_type": "price_breakout",
  "direction": "bullish",
  "confidence": "0.65",
  "rationale": "Price closed above the 30-minute opening range high with confirmation"
}
```

Bearish example:

```json
{
  "strategy_type": "breakout_price_threshold",
  "signal_type": "price_breakdown",
  "direction": "bearish",
  "confidence": "0.65",
  "rationale": "Price closed below the recent 20-candle low with confirmation"
}
```

## Best use case

Breakout strategies work best when a symbol is consolidating and then expands through a meaningful level with participation. They perform poorly in choppy markets where price repeatedly crosses levels without follow-through.

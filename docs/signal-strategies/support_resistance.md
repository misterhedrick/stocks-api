# Support / resistance signal strategy

## Purpose

A support/resistance strategy attempts to trade around price levels where buyers or sellers have previously appeared. Support is a level where price has repeatedly found buyers. Resistance is a level where price has repeatedly found sellers.

This signal family can be used two ways:

- **Breakout mode**: trade continuation when price breaks through support or resistance.
- **Bounce/rejection mode**: trade reversal when price fails to break support or resistance.

For options, bullish signals can map to calls and bearish signals can map to puts.

## Core idea

The scanner first identifies meaningful levels, then watches how price behaves around those levels.

Useful level sources:

- Previous day high.
- Previous day low.
- Premarket high.
- Premarket low.
- Opening range high.
- Opening range low.
- Recent swing highs and swing lows.
- VWAP.
- Round-number levels.
- Manually configured levels.

## Suggested default inputs

Recent swing level config:

```json
{
  "timeframe": "5Min",
  "lookback_candles": 60,
  "swing_window": 3,
  "level_tolerance_percent": "0.10",
  "min_touches": 2,
  "mode": "breakout_or_rejection",
  "dedupe_minutes": 240
}
```

Previous-day level config:

```json
{
  "timeframe": "5Min",
  "levels": ["previous_day_high", "previous_day_low", "premarket_high", "premarket_low"],
  "level_tolerance_percent": "0.10",
  "confirmation_candles": 1,
  "dedupe_minutes": 240
}
```

## Level detection

A swing high is a candle high surrounded by lower highs. A swing low is a candle low surrounded by higher lows.

Example swing high rule:

```text
high[i] > high[i-1]
high[i] > high[i-2]
high[i] > high[i+1]
high[i] > high[i+2]
```

Example swing low rule:

```text
low[i] < low[i-1]
low[i] < low[i-2]
low[i] < low[i+1]
low[i] < low[i+2]
```

Nearby swing highs can be grouped into one resistance zone. Nearby swing lows can be grouped into one support zone.

## Breakout mode

### Bullish resistance breakout

A bullish breakout signal occurs when price closes above a resistance level.

Rules:

1. Resistance level exists.
2. Previous close was at or below resistance.
3. Latest close is above resistance plus a buffer.
4. Optional volume confirms the breakout.
5. Optional market regime confirms bullish conditions.

Example:

```text
previous_close <= resistance_level
latest_close > resistance_level * 1.001
```

### Bearish support breakdown

A bearish breakdown signal occurs when price closes below a support level.

Rules:

1. Support level exists.
2. Previous close was at or above support.
3. Latest close is below support minus a buffer.
4. Optional volume confirms the breakdown.
5. Optional market regime confirms bearish conditions.

Example:

```text
previous_close >= support_level
latest_close < support_level * 0.999
```

## Bounce/rejection mode

### Bullish support bounce

A bullish support bounce occurs when price tests support but rejects lower prices.

Rules:

1. Price trades near or below support.
2. Candle closes back above support.
3. Latest close is above previous close or above candle open.
4. Optional RSI/mean reversion confirms oversold bounce.

Example:

```text
latest_low <= support_level * 1.001
latest_close > support_level
latest_close > latest_open
```

### Bearish resistance rejection

A bearish resistance rejection occurs when price tests resistance but rejects higher prices.

Rules:

1. Price trades near or above resistance.
2. Candle closes back below resistance.
3. Latest close is below previous close or below candle open.
4. Optional RSI/mean reversion confirms overbought rejection.

Example:

```text
latest_high >= resistance_level * 0.999
latest_close < resistance_level
latest_close < latest_open
```

## Confirmation filters

Recommended filters:

- Minimum level strength based on number of touches.
- Minimum distance from current price to avoid chasing stale levels.
- Volume confirmation for breakout mode.
- Reversal candle confirmation for bounce mode.
- Market regime confirmation.
- Avoid signals if level is too close to another conflicting level.

## Level strength scoring

A level can receive a strength score based on:

- Number of touches.
- Recency of touches.
- Volume near the level.
- Whether the level aligns with previous day high/low or premarket high/low.
- Whether price rejected the level sharply before.

Example:

```text
level_strength = touches_score + recency_score + volume_score + prior_day_level_bonus
```

## Avoiding bad signals

Skip the signal when:

- The level has only one weak touch.
- Price is chopping directly around the level with no clear break or rejection.
- Breakout candle is overextended.
- Volume is weak on a breakout.
- Broad market strongly conflicts with the signal.
- The same symbol recently signaled around the same level.

## Confidence scoring

Possible scoring model:

```text
base = 0.55
+ 0.05 if level has at least 2 touches
+ 0.05 if candle confirms breakout/rejection
+ 0.05 if volume confirms
+ 0.05 if market regime confirms
+ 0.05 if level aligns with previous day/premarket level
- 0.05 if price is extended after breakout
cap at 0.82
```

## Signal output

Bullish breakout example:

```json
{
  "strategy_type": "support_resistance",
  "signal_type": "resistance_breakout",
  "direction": "bullish",
  "confidence": "0.68",
  "rationale": "Price closed above resistance with level strength and breakout confirmation"
}
```

Bearish rejection example:

```json
{
  "strategy_type": "support_resistance",
  "signal_type": "resistance_rejection",
  "direction": "bearish",
  "confidence": "0.64",
  "rationale": "Price tested resistance and closed back below the level with rejection confirmation"
}
```

## Best use case

Support/resistance strategies are useful because they define clear decision levels. They are especially valuable when combined with volume, trend, and volatility filters. The first implementation should start with simple previous-day and recent-swing levels before attempting more advanced level clustering.

# Volume-confirmed breakout signal strategy

## Purpose

A volume-confirmed breakout strategy looks for price breaking through an important level while volume confirms participation. The goal is to avoid weak breakouts where price crosses a level but there is not enough demand or supply to continue.

This is a directional signal strategy. Bullish volume breakouts can map to calls. Bearish volume breakdowns can map to puts.

## Core idea

A breakout is more meaningful when volume is above normal. High relative volume suggests more market participants are involved and the move may have better follow-through.

The strategy combines two pieces:

1. Price breaks a level.
2. Volume is meaningfully above recent average volume.

## Suggested default inputs

Starter config:

```json
{
  "timeframe": "5Min",
  "lookback_candles": 20,
  "breakout_buffer_percent": "0.05",
  "relative_volume_min": "1.50",
  "min_confirming_closes": 1,
  "dedupe_minutes": 240
}
```

Opening range version:

```json
{
  "timeframe": "5Min",
  "opening_range_minutes": 30,
  "breakout_buffer_percent": "0.05",
  "relative_volume_min": "1.75",
  "dedupe_minutes": 240
}
```

## Volume calculations

Average candle volume:

```text
average_volume = average(volume over last N completed candles)
```

Relative volume for the latest candle:

```text
relative_volume = latest_volume / average_volume
```

Signal volume requirement:

```text
relative_volume >= relative_volume_min
```

If intraday historical volume by time-of-day is available later, compare volume to the same time of day instead of a simple rolling average. That would avoid false positives early in the session when volume is naturally high.

## Bullish rules

A bullish volume-confirmed breakout should require:

1. Price closes above the breakout level.
2. Price exceeds the level by a minimum buffer.
3. Latest candle volume is above recent average volume.
4. Latest candle closes bullish or near its high.
5. Optional market regime is neutral or bullish.

Example:

```text
latest_close > breakout_level * 1.0005
relative_volume >= 1.50
latest_close > latest_open
```

## Bearish rules

A bearish volume-confirmed breakdown should require:

1. Price closes below the breakdown level.
2. Price falls below the level by a minimum buffer.
3. Latest candle volume is above recent average volume.
4. Latest candle closes bearish or near its low.
5. Optional market regime is neutral or bearish.

Example:

```text
latest_close < breakdown_level * 0.9995
relative_volume >= 1.50
latest_close < latest_open
```

## Level sources

This strategy can use the same level sources as breakout and support/resistance strategies:

- Previous day high or low.
- Premarket high or low.
- Opening range high or low.
- Recent N-candle high or low.
- Manually configured level.
- Recent resistance or support zone.

## Candle quality filters

Volume alone is not enough. A candle with high volume but a large rejection wick may be a failed breakout.

Bullish candle quality checks:

```text
latest_close > latest_open
upper_wick_percent <= max_upper_wick_percent
close_position_in_range >= 0.60
```

Bearish candle quality checks:

```text
latest_close < latest_open
lower_wick_percent <= max_lower_wick_percent
close_position_in_range <= 0.40
```

Where close position in range is:

```text
close_position = (latest_close - latest_low) / (latest_high - latest_low)
```

## Avoiding bad signals

Skip the signal when:

- Volume is high but price closes back inside the range.
- Breakout candle has a large wick against the desired direction.
- Relative volume is high because of a single abnormal print and no price follow-through.
- The symbol has stale or unreliable volume data.
- The same symbol already created a similar breakout signal recently.
- The move is already too extended after the breakout.

## Confidence scoring

Possible scoring model:

```text
base = 0.58
+ 0.05 if relative_volume >= 1.50
+ 0.05 if relative_volume >= 2.00
+ 0.05 if candle closes near high/low in signal direction
+ 0.05 if market regime confirms
+ 0.05 if level is strong
- 0.05 if candle is overextended
cap at 0.85
```

## Signal output

Bullish example:

```json
{
  "strategy_type": "volume_confirmed_breakout",
  "signal_type": "volume_breakout",
  "direction": "bullish",
  "confidence": "0.72",
  "rationale": "Price closed above resistance with 1.8x relative volume and bullish candle confirmation"
}
```

Bearish example:

```json
{
  "strategy_type": "volume_confirmed_breakout",
  "signal_type": "volume_breakdown",
  "direction": "bearish",
  "confidence": "0.72",
  "rationale": "Price closed below support with 1.9x relative volume and bearish candle confirmation"
}
```

## Best use case

Volume-confirmed breakouts are best when a symbol is leaving a consolidation zone or crossing a widely watched level. This signal should be more selective than a plain price-threshold breakout because it requires participation. It can be a strong candidate for higher confidence scoring after enough paper-trade data is collected.

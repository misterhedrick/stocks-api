# Volatility squeeze signal strategy

## Purpose

A volatility squeeze strategy attempts to identify when price volatility has compressed and may soon expand. The idea is that quiet, tight price action often precedes a larger move. The strategy waits for compression first, then looks for a directional breakout.

This is a directional signal strategy after breakout confirmation. Bullish squeeze breakouts can map to calls. Bearish squeeze breakdowns can map to puts.

## Core idea

A squeeze has two phases:

1. **Compression phase**: volatility contracts and price moves inside a tight range.
2. **Expansion phase**: price breaks out of the range and volatility expands.

The scanner should not create a trade signal just because compression exists. Compression is a watch condition. The trade signal should come when price breaks out with confirmation.

## Common squeeze indicators

### Bollinger Band width

Bollinger Band width measures how wide the bands are compared with price.

```text
band_width = (upper_band - lower_band) / middle_band * 100
```

A low band width means volatility is compressed.

### Keltner Channel squeeze

A common squeeze definition is Bollinger Bands inside Keltner Channels.

Squeeze on:

```text
bollinger_upper < keltner_upper
bollinger_lower > keltner_lower
```

Squeeze release:

```text
bollinger_upper > keltner_upper
or
bollinger_lower < keltner_lower
```

### ATR contraction

Average True Range can also identify compression.

```text
current_atr < average_atr * contraction_threshold
```

## Suggested default inputs

Bollinger width version:

```json
{
  "timeframe": "5Min",
  "lookback_candles": 40,
  "bollinger_period": 20,
  "bollinger_stddev": 2,
  "max_band_width_percent": "1.00",
  "breakout_buffer_percent": "0.05",
  "dedupe_minutes": 360
}
```

Keltner squeeze version:

```json
{
  "timeframe": "5Min",
  "bollinger_period": 20,
  "bollinger_stddev": 2,
  "keltner_period": 20,
  "keltner_atr_multiplier": "1.5",
  "require_squeeze_release": true,
  "dedupe_minutes": 360
}
```

## Compression rules

Before creating any directional signal, the scanner should identify that a squeeze exists.

Possible compression requirements:

1. Bollinger Band width below configured maximum.
2. Price range over recent candles is below a maximum threshold.
3. ATR is below recent ATR average.
4. Price has stayed inside a defined range for N candles.

Example:

```text
band_width_percent <= 1.00
recent_range_percent <= 1.25
```

This creates a watch condition, not a trade signal.

## Bullish breakout rules

A bullish squeeze breakout should require:

1. Compression was active recently.
2. Price closes above the squeeze range high or upper band.
3. Breakout exceeds level by a small buffer.
4. Optional volume confirms participation.
5. Optional market regime is neutral or bullish.

Example:

```text
squeeze_active_recently == true
latest_close > range_high * 1.0005
latest_close > previous_close
```

## Bearish breakdown rules

A bearish squeeze breakdown should require:

1. Compression was active recently.
2. Price closes below the squeeze range low or lower band.
3. Breakdown exceeds level by a small buffer.
4. Optional volume confirms participation.
5. Optional market regime is neutral or bearish.

Example:

```text
squeeze_active_recently == true
latest_close < range_low * 0.9995
latest_close < previous_close
```

## Watch-state implementation

This strategy may need state tracking. The scanner can either:

1. Detect squeeze and breakout in the same scan using recent candle history.
2. Store a watch state when compression appears, then create a signal when breakout happens later.

Simpler first implementation:

```text
Look back over recent candles.
If compression existed within last N candles and latest candle breaks range, create signal.
```

More advanced implementation:

```json
{
  "watch_state": "squeeze_active",
  "symbol": "SPY",
  "range_high": "501.25",
  "range_low": "498.80",
  "created_at": "...",
  "expires_at": "..."
}
```

## Avoiding bad signals

Skip the signal when:

- Compression has not actually occurred.
- Breakout candle closes back inside the range.
- Breakout happens on weak volume.
- Bid/ask spread or liquidity is poor.
- Price is already far beyond the breakout level before detection.
- Broad market conflicts strongly with the direction.
- Squeeze is too old and no longer relevant.

## Confidence scoring

Possible scoring model:

```text
base = 0.58
+ 0.05 if compression is very tight
+ 0.05 if breakout candle closes outside range
+ 0.05 if volume confirms
+ 0.05 if market regime confirms
+ 0.05 if ATR begins expanding after compression
- 0.05 if breakout candle is overextended
cap at 0.85
```

## Signal output

Bullish example:

```json
{
  "strategy_type": "volatility_squeeze",
  "signal_type": "squeeze_breakout",
  "direction": "bullish",
  "confidence": "0.70",
  "rationale": "Price broke above the squeeze range after Bollinger Band width compression"
}
```

Bearish example:

```json
{
  "strategy_type": "volatility_squeeze",
  "signal_type": "squeeze_breakdown",
  "direction": "bearish",
  "confidence": "0.70",
  "rationale": "Price broke below the squeeze range after volatility compression"
}
```

## Best use case

Volatility squeeze works best when a symbol has been consolidating and then expands with confirmation. It can generate strong directional signals, but the first implementation should be conservative and require an actual close outside the range plus duplicate suppression.

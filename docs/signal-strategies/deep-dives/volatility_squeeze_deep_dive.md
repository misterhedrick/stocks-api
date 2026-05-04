# Volatility squeeze implementation deep dive

## Implementation objective

Build a two-phase evaluator that detects volatility compression first, then only creates a directional signal after price breaks out of the compressed range.

This strategy should not create entries just because a squeeze exists. A squeeze is a watch condition. The trade signal happens on confirmed expansion.

## Required data

Minimum candle fields:

```text
timestamp, open, high, low, close, volume optional
```

Required indicators:

```text
Bollinger Bands
ATR
Keltner Channels optional
recent range high/low
band width
optional relative volume
market regime optional
```

## Config schema proposal

```json
{
  "type": "volatility_squeeze",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "lookback_candles": 40,
  "bollinger_period": 20,
  "bollinger_stddev": "2.0",
  "max_band_width_percent": "1.00",
  "range_lookback_candles": 20,
  "max_range_percent": "1.25",
  "breakout_buffer_percent": "0.05",
  "require_volume_confirmation": false,
  "relative_volume_min": "1.50",
  "dedupe_minutes": 360
}
```

## Compression detection

### Bollinger Band width

```text
band_width_percent = (upper_band - lower_band) / middle_band * 100
```

Compression passes when:

```text
band_width_percent <= max_band_width_percent
```

### Recent range compression

```text
range_high = max(high over recent range_lookback_candles)
range_low = min(low over recent range_lookback_candles)
range_percent = (range_high - range_low) / latest_close * 100
```

Compression passes when:

```text
range_percent <= max_range_percent
```

### Keltner squeeze optional

Keltner Channels:

```text
middle = EMA(close, period)
upper = middle + ATR(period) * atr_multiplier
lower = middle - ATR(period) * atr_multiplier
```

Squeeze on:

```text
bollinger_upper < keltner_upper
bollinger_lower > keltner_lower
```

This can be added after Bollinger Band width is implemented.

## Squeeze lookback

The latest candle may already be the breakout candle, so compression may not still be active on the latest candle. Check whether compression was active within the recent N candles.

```text
squeeze_active_recently = any(compression_passed over last N candles before latest)
```

Suggested:

```text
compression_recent_candles = 5
```

## Breakout range

Use the compressed range, not necessarily the latest Bollinger Bands.

```text
squeeze_range_high = max(high over compression window)
squeeze_range_low = min(low over compression window)
```

Bullish breakout:

```text
latest_close > squeeze_range_high * (1 + breakout_buffer_percent / 100)
```

Bearish breakdown:

```text
latest_close < squeeze_range_low * (1 - breakout_buffer_percent / 100)
```

## Bullish signal rules

```text
squeeze_active_recently == true
latest_close > squeeze_range_high_with_buffer
latest_close > latest_open
band_width is expanding or range is breaking
optional relative_volume >= relative_volume_min
```

## Bearish signal rules

```text
squeeze_active_recently == true
latest_close < squeeze_range_low_with_buffer
latest_close < latest_open
band_width is expanding or range is breaking
optional relative_volume >= relative_volume_min
```

## Expansion confirmation

A squeeze breakout is stronger if volatility starts expanding.

Possible expansion checks:

```text
current_band_width > previous_band_width
current_atr >= previous_atr
latest_candle_range > average_candle_range
```

Use these as confidence boosts first. Hard-requiring all of them may be too strict.

## Watch-state vs stateless implementation

### Stateless first version

Look back over recent candles and determine whether compression existed recently.

Pros:

```text
simple
no new DB table needed
works inside one scan cycle
```

Cons:

```text
harder to track long compression state
less precise about the exact squeeze range
```

### Stateful later version

Persist active squeeze watches.

```json
{
  "symbol": "SPY",
  "strategy_type": "volatility_squeeze",
  "state": "watching",
  "range_high": "502.10",
  "range_low": "499.80",
  "started_at": "...",
  "expires_at": "..."
}
```

This is more powerful but requires extra state management.

## Features to persist

```json
{
  "band_width_percent": "0.82",
  "previous_band_width_percent": "0.74",
  "range_high": "502.10",
  "range_low": "499.80",
  "range_percent": "0.46",
  "squeeze_active_recently": true,
  "breakout_buffer_percent": "0.05",
  "latest_close": "502.70",
  "relative_volume": "1.42",
  "atr_expanding": true
}
```

## Confidence scoring

```text
base = 0.58
+ 0.05 if compression is very tight
+ 0.05 if latest close breaks range cleanly
+ 0.05 if band width expands
+ 0.05 if ATR expands
+ 0.05 if volume confirms
+ 0.05 if market regime confirms
- 0.05 if breakout candle is overextended
cap at 0.86
```

## Pseudocode

```python
def evaluate_volatility_squeeze(config, candles, indicators, market_regime):
    bollinger = indicators.bollinger(config.bollinger_period, config.bollinger_stddev)
    band_width = calculate_band_width_series(bollinger)

    compression_windows = find_recent_compression_windows(config, candles, band_width)
    if not compression_windows:
        return None

    squeeze_range = latest_relevant_squeeze_range(compression_windows)
    latest = candles[-1]

    if latest.close > buffered_up(squeeze_range.high):
        direction = "bullish"
    elif latest.close < buffered_down(squeeze_range.low):
        direction = "bearish"
    else:
        return None

    if not candle_confirms(direction, latest):
        return None

    confidence = score(...)
    return SignalCandidate(...)
```

## Tests

- compression without breakout returns no signal
- bullish breakout after compression creates signal
- bearish breakdown after compression creates signal
- breakout without prior compression returns no signal
- wick-only breakout returns no signal
- band width expansion boosts confidence
- volume confirmation boosts confidence
- not enough candles returns no signal

## First implementation recommendation

Start with stateless Bollinger Band width + recent range compression. Add Keltner squeeze and persisted watch state later.

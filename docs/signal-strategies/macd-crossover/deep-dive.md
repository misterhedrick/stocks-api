# MACD crossover implementation deep dive

## Implementation objective

Build a MACD evaluator that detects momentum shifts using MACD line, signal line, and histogram behavior. The scanner should support crossover signals first, then later add histogram continuation and zero-line confirmation variants.

## Required data

Minimum candle fields:

```text
timestamp, close, open/high/low optional for candle confirmation
```

Required indicators:

```text
EMA fast
EMA slow
MACD line
MACD signal line
MACD histogram
short price confirmation MA optional
market regime optional
```

## MACD calculation

Standard MACD:

```text
fast_ema = EMA(close, fast_period)
slow_ema = EMA(close, slow_period)
macd_line = fast_ema - slow_ema
signal_line = EMA(macd_line, signal_period)
histogram = macd_line - signal_line
```

Default periods:

```json
{
  "fast_period": 12,
  "slow_period": 26,
  "signal_period": 9
}
```

The indicator engine should compute EMA series once and reuse it.

## Config schema proposal

```json
{
  "type": "macd_crossover",
  "symbols": ["SPY"],
  "timeframe": "5Min",
  "lookback_minutes": 2880,
  "fast_period": 12,
  "slow_period": 26,
  "signal_period": 9,
  "trigger": "signal_line_cross",
  "require_histogram_confirmation": true,
  "require_price_confirmation": true,
  "use_zero_line_as_filter": false,
  "use_zero_line_as_confidence_boost": true,
  "min_histogram_abs": "0.00",
  "dedupe_minutes": 240
}
```

## Warmup requirements

MACD needs enough history for stable EMAs.

Minimum:

```text
slow_period + signal_period + 5
```

Better:

```text
slow_period * 3
```

For 12/26/9 MACD on 5Min candles, use at least 78 candles if possible.

## Bullish signal logic

Bullish crossover:

```text
previous_macd <= previous_signal
current_macd > current_signal
```

Recommended confirmation:

```text
current_histogram > 0
current_histogram > previous_histogram
latest_close > previous_close
```

Optional price confirmation:

```text
latest_close > short_ma
```

## Bearish signal logic

Bearish crossover:

```text
previous_macd >= previous_signal
current_macd < current_signal
```

Recommended confirmation:

```text
current_histogram < 0
current_histogram < previous_histogram
latest_close < previous_close
```

Optional price confirmation:

```text
latest_close < short_ma
```

## Histogram confirmation

Histogram confirms the MACD line has moved to the correct side of signal line.

Bullish:

```text
histogram[-1] > 0
histogram[-1] > histogram[-2]
```

Bearish:

```text
histogram[-1] < 0
histogram[-1] < histogram[-2]
```

If histogram is very close to zero, treat the crossover as weak.

```text
abs(histogram[-1]) >= min_histogram_abs
```

## Zero-line usage

Use zero-line as a confidence boost first, not a hard filter.

Bullish confidence boost:

```text
macd_line > 0
```

Bearish confidence boost:

```text
macd_line < 0
```

If used as hard filter, signals will be later but stronger.

## Avoiding chop

MACD can flip repeatedly in sideways markets.

Reject when:

```text
abs(current_macd - current_signal) < min_line_separation
```

Optional line separation:

```text
line_separation = abs(macd_line - signal_line)
```

The threshold may be symbol-dependent, so a better version uses histogram relative to ATR or close price:

```text
histogram_percent = abs(histogram) / latest_close * 100
```

## Features to persist

```json
{
  "fast_period": 12,
  "slow_period": 26,
  "signal_period": 9,
  "previous_macd": "-0.12",
  "current_macd": "0.04",
  "previous_signal": "-0.05",
  "current_signal": "-0.01",
  "current_histogram": "0.05",
  "previous_histogram": "-0.07",
  "zero_line_confirmed": true,
  "price_confirmation": true
}
```

## Confidence scoring

```text
base = 0.55
+ 0.05 if histogram confirms direction
+ 0.05 if histogram is expanding
+ 0.05 if price confirms direction
+ 0.05 if zero-line confirms
+ 0.05 if market regime confirms
- 0.05 if MACD/signal separation is weak
cap at 0.82
```

## Pseudocode

```python
def evaluate_macd(config, candles, indicators, market_regime):
    macd = indicators.macd(config.fast_period, config.slow_period, config.signal_period)
    if macd.line[-1] is None or macd.signal[-1] is None:
        return None

    bullish = macd.line[-2] <= macd.signal[-2] and macd.line[-1] > macd.signal[-1]
    bearish = macd.line[-2] >= macd.signal[-2] and macd.line[-1] < macd.signal[-1]

    if not bullish and not bearish:
        return None

    direction = "bullish" if bullish else "bearish"

    if config.require_histogram_confirmation and not histogram_confirms(direction, macd.histogram):
        return None

    if config.require_price_confirmation and not price_confirms(direction):
        return None

    return SignalCandidate(...)
```

## Tests

- bullish MACD crossover creates signal
- bearish MACD crossover creates signal
- no crossover returns no signal
- histogram confirmation fails -> no signal
- price confirmation fails -> no signal
- zero-line boosts confidence
- weak line separation reduces confidence or rejects
- not enough candles returns no signal

## First implementation recommendation

Implement signal-line crossover with histogram and price confirmation. Leave zero-line as confidence boost. Add histogram continuation as a separate strategy later.

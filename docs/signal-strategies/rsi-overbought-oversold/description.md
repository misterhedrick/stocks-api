# RSI overbought / oversold signal strategy

## Purpose

An RSI strategy uses the Relative Strength Index to estimate whether a symbol is stretched to the upside or downside. RSI is usually used as a momentum oscillator. It can be used for mean-reversion signals, trend-continuation signals, or reversal-confirmation signals.

For this project, the first implementation should treat RSI as a signal filter and possible standalone reversal signal.

## Core idea

RSI measures the size of recent gains compared with recent losses over a lookback period. Values are bounded from 0 to 100.

Typical interpretation:

- RSI above 70 means overbought.
- RSI below 30 means oversold.
- RSI crossing back above 30 can indicate bullish recovery.
- RSI crossing back below 70 can indicate bearish rejection.

For options entries, blindly buying puts when RSI is above 70 or calls when RSI is below 30 can be dangerous. Strong trends can keep RSI overbought or oversold for a long time. The better implementation waits for a confirmation move.

## Suggested default inputs

Intraday starter config:

```json
{
  "timeframe": "5Min",
  "rsi_period": 14,
  "oversold_level": 30,
  "overbought_level": 70,
  "confirmation_mode": "cross_back_inside",
  "dedupe_minutes": 240
}
```

More aggressive config:

```json
{
  "timeframe": "5Min",
  "rsi_period": 9,
  "oversold_level": 25,
  "overbought_level": 75,
  "confirmation_mode": "reversal_candle",
  "dedupe_minutes": 180
}
```

## Bullish reversal rules

A bullish RSI signal should require:

1. RSI was below the oversold level recently.
2. RSI crosses back above the oversold level, or price prints a reversal candle.
3. Latest close is above previous close.
4. Price is not in a strong bearish trend.
5. Optional market regime is not strongly bearish.

Example:

```text
previous_rsi < 30
current_rsi >= 30
latest_close > previous_close
```

This means the symbol was oversold but is starting to recover.

## Bearish reversal rules

A bearish RSI signal should require:

1. RSI was above the overbought level recently.
2. RSI crosses back below the overbought level, or price prints a rejection candle.
3. Latest close is below previous close.
4. Price is not in a strong bullish trend.
5. Optional market regime is not strongly bullish.

Example:

```text
previous_rsi > 70
current_rsi <= 70
latest_close < previous_close
```

This means the symbol was overbought but is starting to weaken.

## Trend-continuation RSI variant

RSI can also be used for continuation rather than reversal.

Bullish continuation:

```text
rsi between 50 and 70
price above moving average
moving average slope positive
```

Bearish continuation:

```text
rsi between 30 and 50
price below moving average
moving average slope negative
```

This version avoids fading strong trends. It uses RSI to confirm that momentum supports the trend without being extremely extended.

## Confirmation filters

Recommended filters:

- Require RSI cross back inside the overbought/oversold zone.
- Require price confirmation with latest candle close.
- Require price not to be too far from VWAP or moving average.
- Reject signals when trend strength is very high in the opposite direction.
- Avoid entries immediately after major news.
- Avoid duplicate signals for the same symbol/direction/profile.

## Divergence variant

A more advanced RSI signal uses divergence.

Bullish divergence:

```text
price makes lower low
RSI makes higher low
```

Bearish divergence:

```text
price makes higher high
RSI makes lower high
```

Divergence can be powerful but is harder to implement reliably. It requires identifying swing highs and swing lows, so it should be a later enhancement.

## Avoiding bad signals

Skip the signal when:

- RSI is overbought/oversold but still moving farther into the extreme zone.
- Price is trending strongly against the reversal signal.
- The signal candle has a large wick against the desired direction.
- There is no price confirmation.
- The signal is too close to market close for a new entry.

## Confidence scoring

Possible scoring model:

```text
base = 0.50
+ 0.05 if RSI crosses back inside threshold
+ 0.05 if latest candle confirms direction
+ 0.05 if price is near VWAP/mean target
+ 0.05 if market regime does not conflict
- 0.10 if trend strength opposes reversal
cap at 0.75
```

RSI reversal signals should usually have lower confidence caps than trend-following signals because they are often counter-trend.

## Signal output

Bullish example:

```json
{
  "strategy_type": "rsi_reversal",
  "signal_type": "rsi_oversold_recovery",
  "direction": "bullish",
  "confidence": "0.60",
  "rationale": "RSI crossed back above 30 after oversold conditions and price closed higher"
}
```

Bearish example:

```json
{
  "strategy_type": "rsi_reversal",
  "signal_type": "rsi_overbought_rejection",
  "direction": "bearish",
  "confidence": "0.60",
  "rationale": "RSI crossed back below 70 after overbought conditions and price closed lower"
}
```

## Best use case

RSI is best as a confirmation tool or mean-reversion trigger. It should not be used alone in strong trends. The first version should be conservative and require price confirmation before creating signals.

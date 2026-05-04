# Mean reversion signal strategy

## Purpose

A mean reversion strategy attempts to identify when price has moved too far away from a reasonable average and may snap back toward that average. Unlike trend-following or momentum strategies, mean reversion is usually contrarian.

This is a directional signal strategy:

- If price is unusually low relative to its mean, create a bullish reversal signal.
- If price is unusually high relative to its mean, create a bearish reversal signal.

## Core idea

Markets often oscillate around an average price. When price becomes unusually stretched, the move may fade or reverse.

Common mean references:

- VWAP.
- Simple moving average.
- Exponential moving average.
- Bollinger Band middle line.
- Previous close.
- Intraday anchored average.

Common stretch measurements:

- Percent distance from average.
- Standard deviation distance from average.
- Bollinger Band touch or breach.
- Z-score.

## Suggested default inputs

VWAP reversion example:

```json
{
  "timeframe": "5Min",
  "mean_source": "vwap",
  "min_distance_percent": "0.75",
  "confirmation_candles": 1,
  "dedupe_minutes": 240
}
```

Bollinger reversion example:

```json
{
  "timeframe": "5Min",
  "lookback_candles": 20,
  "standard_deviations": 2,
  "confirmation_candles": 1,
  "dedupe_minutes": 240
}
```

## Bullish reversal rules

A bullish mean reversion signal should require:

1. Price is below the mean by at least the configured distance.
2. Price shows evidence of stabilizing or reversing.
3. Latest candle closes above its low or above prior candle close.
4. Optional RSI or momentum exhaustion confirms oversold conditions.
5. Optional broad market is not strongly bearish.

Example:

```text
current_price < vwap * 0.9925
latest_close > previous_close
latest_close > latest_open
```

Bollinger version:

```text
latest_low < lower_band
latest_close > lower_band
```

This means price pierced the lower band but closed back inside, suggesting rejection of lower prices.

## Bearish reversal rules

A bearish mean reversion signal should require:

1. Price is above the mean by at least the configured distance.
2. Price shows evidence of stalling or reversing lower.
3. Latest candle closes below its high or below prior candle close.
4. Optional RSI or momentum exhaustion confirms overbought conditions.
5. Optional broad market is not strongly bullish.

Example:

```text
current_price > vwap * 1.0075
latest_close < previous_close
latest_close < latest_open
```

Bollinger version:

```text
latest_high > upper_band
latest_close < upper_band
```

## Confirmation filters

Mean reversion has high risk when a strong trend is in progress. Add filters to avoid fading strong trend days.

Recommended filters:

- Reject bullish reversals if broad market is strongly bearish.
- Reject bearish reversals if broad market is strongly bullish.
- Require reversal candle confirmation.
- Require price to move back inside a band or toward the mean.
- Avoid entries immediately after news-driven moves.
- Avoid signals when volume expansion confirms the original move.

## Trend filter

Do not blindly fade strong trends. A useful filter is average slope.

Bullish reversion should be avoided when:

```text
long_ma_slope < strong_negative_threshold
```

Bearish reversion should be avoided when:

```text
long_ma_slope > strong_positive_threshold
```

## Profit expectation

The expected move is usually back toward the mean, not a full trend reversal. The scanner should record the mean level as a potential target reference.

Example features:

```json
{
  "mean_source": "vwap",
  "mean_price": "502.40",
  "distance_from_mean_percent": "0.82"
}
```

## Avoiding bad signals

Skip the signal when:

- Price is trending strongly in one direction.
- Reversion candle has not confirmed.
- The move is news-driven or earnings-driven.
- Underlying spread/liquidity is poor.
- Broad market is strongly aligned with the move being faded.
- Price is far from the mean but still accelerating away from it.

## Confidence scoring

Possible scoring model:

```text
base = 0.50
+ 0.05 if distance from mean exceeds threshold
+ 0.05 if candle closes back toward mean
+ 0.05 if RSI confirms oversold/overbought
+ 0.05 if market regime does not conflict
- 0.10 if long moving average slope strongly opposes reversal
cap at 0.75
```

Mean reversion confidence should usually be capped lower than trend-following because fading strong moves can be dangerous.

## Signal output

Bullish example:

```json
{
  "strategy_type": "mean_reversion",
  "signal_type": "vwap_reversion",
  "direction": "bullish",
  "confidence": "0.60",
  "rationale": "Price traded 0.85% below VWAP and closed back toward VWAP with reversal confirmation"
}
```

Bearish example:

```json
{
  "strategy_type": "mean_reversion",
  "signal_type": "vwap_reversion",
  "direction": "bearish",
  "confidence": "0.60",
  "rationale": "Price traded 0.90% above VWAP and closed back below the prior candle close"
}
```

## Best use case

Mean reversion works best in range-bound or choppy markets where price repeatedly stretches away from and returns to a central average. It performs poorly on strong trend days. It should use conservative sizing, strict confirmation, and clear stop logic.

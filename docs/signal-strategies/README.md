# Signal strategy planning docs

This folder documents entry signal strategy families implemented in the paper trading system and the design references used to build them.

Important terminology:

- **Signal strategy**: decides whether market conditions are bullish, bearish, neutral, or not actionable.
- **Option strategy**: decides what option structure to trade after a signal exists, such as long call, long put, debit spread, credit spread, straddle, or iron condor.

The current app mostly maps bullish signals to long calls and bearish signals to long puts. These docs focus only on signal logic.

## Current app status

The evaluator-backed scanner path is implemented for:

```text
momentum_rate_of_change
moving_average
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
```

Legacy direct scanner paths still exist for `price_threshold`, `percent_change`, and `trend_confirmation`.

## Strategy files

- `moving_average_trend_following.md`
- `momentum_rate_of_change.md`
- `breakout_price_threshold.md`
- `mean_reversion.md`
- `rsi_overbought_oversold.md`
- `macd_crossover.md`
- `support_resistance.md`
- `volume_confirmed_breakout.md`
- `volatility_squeeze.md`

## Common implementation shape

Each strategy should eventually produce a normalized signal record with fields like:

```json
{
  "symbol": "SPY",
  "strategy_type": "moving_average",
  "direction": "bullish",
  "confidence": "0.68",
  "signal_type": "moving_average_setup",
  "rationale": "Short moving average crossed above long moving average with price confirmation",
  "features": {
    "timeframe": "5Min",
    "lookback_minutes": 1440
  }
}
```

The scanner should not directly decide which option contract to buy. It should only create a clean directional signal. Contract selection and risk filters should remain separate.

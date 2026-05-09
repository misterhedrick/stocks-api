# Signal strategy planning docs

This folder documents entry signal strategy families implemented in the paper trading system and the design references used to build them.

Important terminology:

- **Signal strategy**: decides whether market conditions are bullish, bearish, neutral, or not actionable.
- **Option strategy**: decides what option structure to trade after a signal exists, such as long call, long put, debit spread, credit spread, straddle, or iron condor.

The current app mostly maps bullish signals to long calls and bearish signals to long puts. These docs focus only on signal logic.

## Current app status

The scanner path is evaluator-backed for:

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

Legacy direct scanner paths for `price_threshold`, `percent_change`, and `trend_confirmation` have been removed from active strategy seeding and should not be used for new strategy configs.

## Folder Layout

Each strategy has its own folder:

```text
<strategy-folder>/
  description.md
  deep-dive.md
  tuning.md
  implementation-note.md  # only where an implementation note exists
```

Strategy folders:

| Folder | Scanner type | Notes |
|---|---|---|
| `moving-average-trend-following/` | `moving_average` | Trend/crossover strategy |
| `momentum-rate-of-change/` | `momentum_rate_of_change` | Short-term momentum strategy |
| `breakout-price-threshold/` | `breakout_price_threshold` | Includes implementation note |
| `mean-reversion/` | `mean_reversion` | Bollinger Band reversion strategy |
| `rsi-overbought-oversold/` | `rsi_reversal` | RSI reversal strategy |
| `macd-crossover/` | `macd_crossover` | MACD signal-line crossover strategy |
| `support-resistance/` | `support_resistance` | Includes implementation note |
| `volume-confirmed-breakout/` | `volume_confirmed_breakout` | Includes implementation note |
| `volatility-squeeze/` | `volatility_squeeze` | Includes implementation note |
| `shared/` | shared | Shared indicator engine and option selection notes |

Use `tuning.md` when making human-reviewed or AI-assisted strategy adjustments. The tuning files are written to support small, evidence-driven changes and include AI adjustment contracts that can be reviewed before applying any config change.

## Common implementation shape

Each evaluator-backed strategy produces a normalized signal candidate that the scanner turns into records with fields like:

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

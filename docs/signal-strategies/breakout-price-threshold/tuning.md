# Breakout Price Threshold Tuning

Use this guide when changing `scanner.type = breakout_price_threshold` strategies. The goal is to catch continuation after price breaks a meaningful level without buying false breakouts.

## Evidence to Review

- `signals.market_context` for breakout level, buffer, threshold crossing, and distance percent.
- `job_runs.details.no_signal_reasons` for range or price confirmation misses.
- Rejected previews after sharp moves, especially notional and spread.
- Round trips grouped by symbol and direction.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `range_lookback_candles` | Lower value | Higher value |
| `breakout_buffer_percent` | Lower value | Higher value |
| `max_breakout_distance_percent` | Higher value | Lower value |
| `require_price_confirmation` | `false` | `true` |
| `price_above` / `price_below` | Closer to market | Farther from market |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `5Min`, 20-candle range, `0.05%` buffer, max distance `3.0%`, price confirmation on.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = breakout_price_threshold`.
- Breakout entries can have wider spreads after the move; do not loosen spread limits without checking fill quality.
- If the scanner is good but contracts are too expensive, adjust strike/DTE/preview profile before changing the breakout rule.

## Human Tuning Rules

- Too few signals: lower `breakout_buffer_percent` or shorten `range_lookback_candles`.
- Too many false breakouts: increase buffer, require confirmation, or increase range lookback.
- Chasing extended moves: lower `max_breakout_distance_percent`.
- Good breakouts but poor exits: evaluate profit target and stop loss separately from entry.

## AI Adjustment Contract

```json
{
  "strategy_type": "breakout_price_threshold",
  "evidence_window": "last_500_signals",
  "change": {
    "scanner.breakout_buffer_percent": "0.075",
    "scanner.max_breakout_distance_percent": "2.0"
  },
  "reason": "False breakouts and extended entries dominated recent losses.",
  "risk": "May reduce signal count during quieter sessions.",
  "rollback": {
    "scanner.breakout_buffer_percent": "0.05",
    "scanner.max_breakout_distance_percent": "3.0"
  }
}
```

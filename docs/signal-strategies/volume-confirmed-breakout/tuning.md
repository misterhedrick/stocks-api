# Volume Confirmed Breakout Tuning

Use this guide when changing `scanner.type = volume_confirmed_breakout` strategies. This strategy should be stricter than a plain breakout because volume is used as participation confirmation.

## Evidence to Review

- `signals.market_context` for relative volume, range high/low, breakout buffer, and distance percent.
- No-signal reasons for volume threshold failures versus price threshold failures.
- Whether high-volume signals improve fills or simply arrive after expensive option repricing.
- Realized returns grouped by symbol.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `range_lookback_candles` | Lower value | Higher value |
| `volume_lookback_candles` | Lower value | Higher value |
| `min_relative_volume` | Lower value | Higher value |
| `breakout_buffer_percent` | Lower value | Higher value |
| `max_breakout_distance_percent` | Higher value | Lower value |
| `require_candle_confirmation` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `5Min`, 20-candle range, 20-candle volume lookback, `1.25` relative volume, `0.05%` buffer.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = volume_confirmed_breakout`.
- High volume can widen option spreads; monitor rejection and fill slippage.
- Keep `max_spread_percent` honest. A high-volume breakout with an ugly option chain is still a bad paper data point.

## Human Tuning Rules

- Too few signals: lower `min_relative_volume` toward `1.10` or shorten volume lookback.
- Too many low-quality signals: raise `min_relative_volume` or require candle confirmation.
- Entries too late: lower volume lookback or buffer slightly, but keep max distance capped.
- Preview failures: tune contract selection only after confirming the underlying signal quality.

## AI Adjustment Contract

```json
{
  "strategy_type": "volume_confirmed_breakout",
  "evidence_window": "last_20_market_days",
  "change": {
    "scanner.min_relative_volume": "1.40"
  },
  "reason": "Breakouts below 1.40 relative volume had negative average return.",
  "risk": "Fewer signals on quiet symbols.",
  "rollback": {
    "scanner.min_relative_volume": "1.25"
  }
}
```

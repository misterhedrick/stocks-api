# Volatility Squeeze Tuning

Use this guide when changing `scanner.type = volatility_squeeze` strategies. This strategy looks for compression followed by expansion, so both the compression rule and breakout rule matter.

## Evidence to Review

- `signals.market_context` for band width, compression ratio, range high/low, breakout level, and distance percent.
- No-signal reasons for missing compression versus missing breakout.
- Whether signals occur after option premiums already expanded.
- Performance by symbol; squeeze behavior can be very symbol-specific.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `bollinger_period` | Lower value | Higher value |
| `bollinger_stddev` | Lower value | Higher value |
| `squeeze_lookback_candles` | Lower value | Higher value |
| `range_lookback_candles` | Lower value | Higher value |
| `compression_ratio_threshold` | Higher value | Lower value |
| `max_band_width_percent` | Higher value / remove | Lower value |
| `breakout_buffer_percent` | Lower value | Higher value |
| `max_breakout_distance_percent` | Higher value | Lower value |
| `require_price_confirmation` | `false` | `true` |

Default seeded posture: `5Min`, Bollinger `20/2.0`, 20-candle squeeze and range, compression ratio `0.90`, buffer `0.05%`.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = volatility_squeeze`.
- Watch premium expansion after the breakout; notional caps are especially important here.
- If signal quality is good but entries are expensive, tune option selection before loosening squeeze detection.
- Global option selection settings (`OPTIONS_MIN_DTE`, `OPTIONS_TARGET_DTE`, `OPTIONS_MAX_DTE`, `OPTIONS_MAX_SPREAD_PCT`, `OPTIONS_ALLOW_MISSING_OI_SYMBOLS`) apply across all profiles and sit above profile-level limits. See `docs/signal-strategies/shared/option-selection.md`.

## Human Tuning Rules

- Too few signals: raise `compression_ratio_threshold` or reduce squeeze lookback.
- Too many weak setups: lower `compression_ratio_threshold`, set `max_band_width_percent`, or increase breakout buffer.
- Chasing expanded moves: lower `max_breakout_distance_percent`.
- Winners need room: evaluate stop/target and `max_hold_hours` after enough closed trade cases.

## AI Adjustment Contract

```json
{
  "strategy_type": "volatility_squeeze",
  "evidence_window": "last_500_signals",
  "change": {
    "scanner.compression_ratio_threshold": "0.80",
    "scanner.max_breakout_distance_percent": "3.0"
  },
  "reason": "Recent loose squeeze setups fired without durable compression and chased extended breakouts.",
  "risk": "Lower signal volume.",
  "rollback": {
    "scanner.compression_ratio_threshold": "0.90",
    "scanner.max_breakout_distance_percent": "4.0"
  }
}
```

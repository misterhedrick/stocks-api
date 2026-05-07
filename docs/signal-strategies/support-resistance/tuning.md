# Support Resistance Tuning

Use this guide when changing `scanner.type = support_resistance` strategies. This strategy can trade bounces, rejections, and breakouts around detected or configured levels.

## Evidence to Review

- `signals.market_context` for level kind, value, touches, tolerance, breakout buffer, and distance percent.
- Whether losses came from bad level detection, bad mode, or option execution.
- Symbol-specific behavior; some names respect swing levels more cleanly than others.
- No-signal reasons for insufficient touches or confirmation failures.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `mode` | `both` | `breakout` or `rejection` only |
| `swing_window` | Lower value | Higher value |
| `lookback_candles` | Lower value | Higher value |
| `min_touches` | Lower value | Higher value |
| `level_tolerance_percent` | Higher value | Lower value |
| `breakout_buffer_percent` | Lower value | Higher value |
| `max_distance_percent` | Higher value | Lower value |
| `require_candle_confirmation` | `false` | `true` |
| `support_levels` / `resistance_levels` | Add manual levels | Remove stale levels |

Default seeded posture: `5Min`, 60 candles, swing window 3, min touches 2, tolerance `0.20%`, breakout buffer `0.075%`.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = support_resistance`.
- Manual levels should be refreshed when the market structure changes.
- Keep position caps tight if testing both bounce/rejection and breakout modes at once.
- Global option selection settings (`OPTIONS_MIN_DTE`, `OPTIONS_TARGET_DTE`, `OPTIONS_MAX_DTE`, `OPTIONS_MAX_SPREAD_PCT`, `OPTIONS_ALLOW_MISSING_OI_SYMBOLS`) apply across all profiles and sit above profile-level limits. See `docs/signal-strategies/shared/option-selection.md`.

## Human Tuning Rules

- Too few signals: lower `min_touches`, widen tolerance, or use `both`.
- Too many noisy levels: increase `swing_window`, raise `min_touches`, or lower tolerance.
- Breakouts fail but bounces work: switch `mode` toward `rejection`.
- Bounces fail in trend days: use `breakout` mode or add external market regime checks.

## AI Adjustment Contract

```json
{
  "strategy_type": "support_resistance",
  "evidence_window": "last_100_trade_cases",
  "change": {
    "scanner.mode": "breakout",
    "scanner.min_touches": 3
  },
  "reason": "Rejection-mode trades lost money while breakout trades had positive average return.",
  "risk": "Will ignore bounce setups until reverted.",
  "rollback": {
    "scanner.mode": "both",
    "scanner.min_touches": 2
  }
}
```

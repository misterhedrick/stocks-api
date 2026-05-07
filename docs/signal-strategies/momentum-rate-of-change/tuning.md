# Momentum Rate Of Change Tuning

Use this guide when changing `scanner.type = momentum_rate_of_change` strategies. This strategy is sensitive to short-term noise, so tune with recent market context and avoid overfitting to one fast move.

## Evidence to Review

- Signal volume and non-trade reasons in `/api/v1/automation/learning-report`.
- `signals.market_context` for `lookback_minutes`, `change_percent`, `short_average_window`, and latest candle confirmation.
- Preview rejection reasons in `order_intents.preview`.
- Round-trip performance by symbol; momentum often behaves differently on index ETFs versus single names.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `lookback_minutes` | Lower value | Higher value |
| `change_above_percent` | Lower value | Higher value |
| `change_below_percent` | Closer to zero | More negative |
| `short_average_window` | Lower value | Higher value |
| `require_latest_candle_confirmation` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `1Min`, 30-minute lookback, about `0.175%` move threshold, EMA confirmation on.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = momentum_rate_of_change`.
- Momentum entries may need tighter `max_spread_percent` because timing matters.
- Keep DTE short enough for responsiveness but avoid contracts with poor liquidity.
- Global option selection settings (`OPTIONS_MIN_DTE`, `OPTIONS_TARGET_DTE`, `OPTIONS_MAX_DTE`, `OPTIONS_MAX_SPREAD_PCT`, `OPTIONS_ALLOW_MISSING_OI_SYMBOLS`) apply across all profiles and sit above profile-level limits. See `docs/signal-strategies/shared/option-selection.md`.
- If fills are consistently poor, tune option selection before loosening scanner thresholds.

## Human Tuning Rules

- Too few signals: lower `change_above_percent` or reduce `lookback_minutes`.
- Too many false positives: raise the change threshold, require candle confirmation, or increase `short_average_window`.
- Good entries but missed previews: loosen preview notional/spread limits only after checking quote quality.
- Momentum fades quickly: consider tighter exits or lower profit targets before changing entry rules.

## AI Adjustment Contract

```json
{
  "strategy_type": "momentum_rate_of_change",
  "evidence_window": "last_20_market_cycles",
  "change": {
    "scanner.change_above_percent": "0.20",
    "scanner.dedupe_minutes": 90
  },
  "reason": "High preview volume with weak realized follow-through.",
  "risk": "May miss smaller trend starts.",
  "rollback": {
    "scanner.change_above_percent": "0.175",
    "scanner.dedupe_minutes": 60
  }
}
```

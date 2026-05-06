# Mean Reversion Tuning

Use this guide when changing `scanner.type = mean_reversion` strategies. This strategy looks for price stretching outside a Bollinger Band and recovering, so it is most vulnerable during strong trend days.

## Evidence to Review

- `signals.market_context` for band values, distance to middle band, and price confirmation.
- Losses during one-direction trend days.
- Holding time and exit reasons in trade lifecycle views.
- Preview rejections caused by spreads after volatility expansion.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `bollinger_period` | Lower value | Higher value |
| `bollinger_stddev` | Lower value | Higher value |
| `max_distance_to_middle_percent` | Higher value | Lower value |
| `require_price_confirmation` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `5Min`, Bollinger `20`, stddev `2.0`, price confirmation on, max distance to middle `2.0%`.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = mean_reversion`.
- Keep order size small because failed reversions can move quickly.
- For reversal strategies, exit tuning often matters as much as entry tuning.

## Human Tuning Rules

- Too few signals: reduce `bollinger_stddev` or increase `max_distance_to_middle_percent`.
- Too many early entries: require price confirmation and lower `max_distance_to_middle_percent`.
- Trend-day losses: add/strengthen market regime filtering in strategy config or disable that symbol temporarily.
- Winners reverse before target: lower profit target or add shorter `max_hold_hours`.

## AI Adjustment Contract

```json
{
  "strategy_type": "mean_reversion",
  "evidence_window": "last_30_closed_trade_cases",
  "change": {
    "scanner.max_distance_to_middle_percent": "1.25"
  },
  "reason": "Large distance-to-middle entries had poor realized returns.",
  "risk": "May reject valid oversold/overbought extremes.",
  "rollback": {
    "scanner.max_distance_to_middle_percent": "2.0"
  }
}
```

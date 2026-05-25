# Moving Average Trend Following Tuning

Use this guide when changing `scanner.type = moving_average` strategies. Tune one small thing at a time and compare the next paper-trading sample against prior `signals`, `order_intents`, fills, and `trade_cases`.

## Evidence to Review

- Signal volume by strategy and symbol in `/api/v1/automation/learning-report`.
- `job_runs.details.no_signal_reasons` for short/long window, slope, price confirmation, market regime, or dedupe blocks.
- `signals.market_context` for `short_window`, `long_window`, trigger, averages, slope, and price confirmation.
- `order_intents.preview` for option rejection reasons, spread, notional, DTE, and open interest.
- Realized round trips in `/api/v1/automation/performance?limit=500`.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `short_window` | Lower value | Higher value |
| `long_window` | Lower value | Higher value |
| `trigger` | `bullish_trend` / `bearish_trend` | `bullish_cross` / `bearish_cross` |
| `min_change_percent` | Lower value | Higher value |
| `require_short_average_slope` | `false` | `true` |
| `require_price_confirmation` | `false` | `true` |
| `market_regime.enabled` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture is selective: `5/20`, `5Min`, `bullish_cross`, slope and price confirmation on.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = moving_average`.
- If many good signals are rejected, consider profile env changes such as `PREVIEW_PROFILE_MOVING_AVERAGE_MAX_ESTIMATED_NOTIONAL`, `MIN_OPEN_INTEREST`, or `MAX_SPREAD_PERCENT`.
- Global option selection settings (`OPTIONS_MIN_DTE`, `OPTIONS_TARGET_DTE`, `OPTIONS_MAX_DTE`, `OPTIONS_MAX_SPREAD_PCT`, `OPTIONS_ALLOW_MISSING_OI_SYMBOLS`) apply across all profiles and sit above profile-level limits. See `docs/signal-strategies/shared/option-selection.md`.
- Keep `scanner.submit.max_contracts_per_order = 1` while tuning.
- Tune exits before changing entries if signals look good but round trips lose after entry.

## Human Tuning Rules

- Too few signals: lower `min_change_percent`, shorten `dedupe_minutes`, or test `bullish_trend`/`bearish_trend` on paper only.
- Too many losing signals: require crossover triggers, increase `min_change_percent`, keep market regime on, or increase `dedupe_minutes`.
- Late entries: shorten windows slightly, for example `4/18`, but watch false positives.
- Whipsaw losses: lengthen windows, for example `8/21`, and keep price confirmation enabled.

## AI Adjustment Contract

An AI may propose a JSON patch like:

```json
{
  "strategy_type": "moving_average",
  "evidence_window": "last_500_order_intents",
  "change": {
    "scanner.min_change_percent": "0.08",
    "scanner.dedupe_minutes": 120
  },
  "reason": "Signal count was low and no_signal_reasons were dominated by min_change_percent.",
  "risk": "Could increase whipsaw entries.",
  "rollback": {
    "scanner.min_change_percent": "0.10",
    "scanner.dedupe_minutes": 240
  }
}
```

Do not let AI apply changes without human review. Prefer one or two keys per tuning pass.

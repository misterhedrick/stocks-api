# RSI Reversal Tuning

Use this guide when changing `scanner.type = rsi_reversal` strategies. RSI reversal is a mean-reversion setup; it should usually be tuned differently from breakout or momentum strategies.

## Evidence to Review

- `signals.market_context` for `previous_rsi`, `latest_rsi`, threshold, confirmation mode, and trend average.
- Losing trades that entered against strong trends.
- `job_runs.details.no_signal_reasons` for price confirmation or threshold misses.
- Open lots held too long after reversal entries.

## Scanner Knobs

| Key | Looser / more signals | Stricter / fewer signals |
|---|---|---|
| `rsi_period` | Lower value | Higher value |
| `oversold_level` | Higher value | Lower value |
| `overbought_level` | Lower value | Higher value |
| `confirmation_mode` | `reversal_candle` | `cross_back_inside` |
| `require_price_confirmation` | `false` | `true` |
| `trend_average_window` | Remove / lower | Add / higher |
| `reject_trend_conflict` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `5Min`, RSI 14, oversold 35, overbought 65, cross-back-inside confirmation.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = rsi_reversal`.
- Reversal trades can fail hard in trends; keep premium caps and position caps conservative.
- If entries are early but thesis later works, tune confirmation before widening stop loss.

## Human Tuning Rules

- Too few signals: move thresholds inward, for example oversold `35` to `38`, or test `reversal_candle`.
- Too many trend-fighting losses: enable `trend_average_window` and `reject_trend_conflict`.
- Exits cut winners early: review `profit_target_percent` and `max_hold_hours` evidence before changing RSI.
- Signals arrive late: reduce `rsi_period` slightly, but expect more noise.

## AI Adjustment Contract

```json
{
  "strategy_type": "rsi_reversal",
  "evidence_window": "last_500_signals",
  "change": {
    "scanner.reject_trend_conflict": true,
    "scanner.trend_average_window": 20
  },
  "reason": "Most losing RSI entries were against the 20-period trend average.",
  "risk": "Will reduce reversal signal count.",
  "rollback": {
    "scanner.reject_trend_conflict": false,
    "scanner.trend_average_window": null
  }
}
```

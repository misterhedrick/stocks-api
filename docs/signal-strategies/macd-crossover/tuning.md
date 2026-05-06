# MACD Crossover Tuning

Use this guide when changing `scanner.type = macd_crossover` strategies. MACD is slower than momentum and can reduce noise, but delayed entries are a common failure mode.

## Evidence to Review

- Signal timing versus the underlying move in `signals.market_context`.
- Losses caused by late entries after large candles.
- `job_runs.details.no_signal_reasons` for price or histogram confirmation misses.
- Performance by symbol and timeframe.

## Scanner Knobs

| Key | Looser / faster | Stricter / slower |
|---|---|---|
| `fast_period` | Lower value | Higher value |
| `slow_period` | Lower value | Higher value |
| `signal_period` | Lower value | Higher value |
| `require_histogram_confirmation` | `false` | `true` |
| `require_price_confirmation` | `false` | `true` |
| `dedupe_minutes` | Lower value | Higher value |

Default seeded posture: `5Min`, `12/26/9`, price confirmation on, histogram confirmation off.

## Preview and Risk Knobs

- Use `scanner.preview.preview_profile = macd_crossover`.
- MACD signals can arrive after volatility expands; watch spread and notional rejection rates.
- If options are expensive after the move, lower max premium or require tighter spreads rather than chasing.

## Human Tuning Rules

- Too few signals: disable histogram confirmation if enabled or shorten periods slightly.
- Late signals: try `8/21/5` on paper, but compare false positives.
- Too many weak crossovers: enable histogram confirmation or increase `signal_period`.
- Good signals but bad fills: tighten spread or lower premium caps.

## AI Adjustment Contract

```json
{
  "strategy_type": "macd_crossover",
  "evidence_window": "last_100_round_trips",
  "change": {
    "scanner.require_histogram_confirmation": true
  },
  "reason": "Losing entries were clustered around flat histogram crossovers.",
  "risk": "Fewer but later signals.",
  "rollback": {
    "scanner.require_histogram_confirmation": false
  }
}
```

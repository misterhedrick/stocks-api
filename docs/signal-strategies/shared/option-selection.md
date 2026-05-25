# Option Contract Selection — Global Settings

These settings apply to every strategy regardless of scanner type or preview profile. They sit above the per-profile limits and control which contracts are even considered before profile-level spread/notional/OI caps are applied.

## DTE Window

| Env var | Default | Purpose |
|---|---|---|
| `OPTIONS_MIN_DTE` | `7` | Minimum days to expiration sent to Alpaca. Contracts expiring in fewer days are filtered out at the API request level. |
| `OPTIONS_TARGET_DTE` | `14` | Preferred DTE. Candidates are scored by distance from this target, so a 14-DTE contract ranks above a 45-DTE contract when all else is equal. |
| `OPTIONS_MAX_DTE` | `45` | Maximum DTE sent to Alpaca. Avoids selecting long-dated illiquid contracts. |

These defaults apply **only when the strategy's `scanner.preview` config does not set an explicit `min_days_to_expiration` / `max_days_to_expiration`**. If a strategy has explicit DTE filters in its scanner config those values override the global window entirely.

**If you want the global window to enforce a 7-day floor for all strategies, remove or set `min_days_to_expiration` to `null` in the strategy config.** Strategies seeded before this setting was added carry `min_days_to_expiration=2` and will continue using that value until re-seeded.

Tuning guidance:

- `OPTIONS_MIN_DTE=7` avoids ultra-near-term contracts that have poor quote quality on paper feeds.
- `OPTIONS_TARGET_DTE=14` is a good starting point for short-term directional plays. Raise to `21`–`30` if you want more time decay buffer.
- `OPTIONS_MAX_DTE=45` can be raised to `60` for strategies that want more time, but very long-dated contracts have wider spreads on paper feeds.

## Spread Filter — OR Logic

| Env var | Default | Purpose |
|---|---|---|
| `OPTIONS_MAX_SPREAD_PCT` | `0.15` | Maximum relative spread as a fraction (e.g. `0.15` = 15%). |
| `OPTIONS_MAX_CONTRACT_NOTIONAL` | `5000` | Default maximum estimated contract notional when a strategy/profile does not set a tighter value. |

The absolute spread cap (`STRATEGY_MAX_SPREAD` or the per-profile `MAX_SPREAD`) still applies. A candidate **passes** the spread check if:

```
spread ≤ absolute_cap   OR   spread / mid ≤ OPTIONS_MAX_SPREAD_PCT
```

Both thresholds must be exceeded to reject a candidate. This means a more expensive option (e.g. $3.20 mid) with a $0.40 spread (12.5%) passes even though $0.40 exceeds the $0.35 absolute cap, because the relative spread is within 15%.

Tuning guidance:

- `OPTIONS_MAX_SPREAD_PCT=0.15` (15%) is a reasonable default for paper trading.
- Lower to `0.10` if you want tighter fills at the cost of fewer accepted contracts.
- Raise to `0.20`–`0.25` only if you are deliberately collecting wide-spread outcomes for diagnostics. Not recommended for performance measurement.
- The effective relative threshold used is `min(OPTIONS_MAX_SPREAD_PCT, per_profile_MAX_SPREAD_PERCENT / 100)` when a profile also sets `MAX_SPREAD_PERCENT`.

## Missing Open Interest Allowlist

| Env var | Default | Purpose |
|---|---|---|
| `OPTIONS_MIN_OPEN_INTEREST` | `50` | Default minimum open interest when a strategy/profile does not set a tighter value. |
| `OPTIONS_ALLOW_MISSING_OI_SYMBOLS` | `SPY,QQQ` | Comma-separated list of symbols that may skip the missing open interest rejection if quote quality passes. |

SPY and QQQ often have `null` open interest on the Alpaca paper data feed even when the contract is actively quoted. Allowlisting them prevents unnecessary rejections. The allowlist only bypasses the *missing* OI check — if OI is present and below `MIN_OPEN_INTEREST` the candidate is still rejected.

Allowlisted symbols must still pass:

- A usable two-sided bid/ask quote.
- The absolute or relative spread filter.

Single-name stocks like AAPL, MSFT, and NVDA are **not** in the allowlist by default. Add them only if confirmed that OI data is structurally absent on your feed.

## Candidate Scoring and Breadth

| Env var | Default | Purpose |
|---|---|---|
| `OPTIONS_CANDIDATE_LIMIT` | `100` | Maximum number of option contracts requested, ranked, and quote-checked per selection attempt. `OPTIONS_MAX_CANDIDATES` is still accepted for backward compatibility. |
| `OPTIONS_DIAGNOSTIC_CANDIDATE_LIMIT` | `10` | Maximum number of rejected candidate detail records attached to diagnostics/logs. |

After Alpaca returns contracts, they are ranked before the candidate cap. Ranking prefers contracts with usable/open-interest data first, then strikes nearest the target or underlying price, then the preferred DTE window, higher open interest, and stable symbol ordering. The selector still rejects any candidate that fails hard quote, liquidity, spread, or notional filters. Among candidates that pass all constraints, the best quote wins by lower spread percentage, lower spread, lower estimated notional, better quote size, and higher open interest.

Raising `OPTIONS_CANDIDATE_LIMIT` increases the chance of finding a passing contract in wide markets at the cost of more Alpaca quote API calls per preview cycle. It should be the first tuning step before loosening `OPTIONS_MIN_OPEN_INTEREST`, `OPTIONS_MAX_SPREAD_PCT`, or `OPTIONS_MAX_CONTRACT_NOTIONAL`.

Detailed rejected-candidate diagnostics are intentionally capped. `OPTIONS_DIAGNOSTIC_CANDIDATE_LIMIT` only controls how many rejected candidates are logged/stored for debugging; it does not change how many candidates are evaluated. Use it to increase the sample only when you need a short-term investigation; keeping it low avoids giant Render logs.

## Preview Attempts

| Env var | Default | Purpose |
|---|---|---|
| `OPTIONS_PREVIEW_MAX_ATTEMPTS` | `3` | Maximum failed market-cycle preview attempts for a signal before it is marked `preview_rejected` and skipped by future cycles. |

Market-cycle preview failures now update the source signal with `preview_attempts`, `last_previewed_at`, `last_preview_error_code`, `last_preview_error`, and structured `preview_rejection_reasons` when the option selector provides reason counts. Seeing `preview_rejected` signals is expected when no contract passes quote, liquidity, spread, or notional filters after the configured number of attempts.

## Interaction with Per-Profile Settings

The global settings and per-profile settings work at different layers:

```
1. Alpaca API request:  filtered by DTE window (global) + expiration_date from strategy config
2. Candidate query/sort/cap:  request up to OPTIONS_CANDIDATE_LIMIT contracts, rank by OI availability, strike proximity, DTE score, and liquidity, then quote-check up to that same limit
3. Quote check per candidate:
   a. OI check:         MIN_OPEN_INTEREST (profile) + missing-OI allowlist (global)
   b. Quote check:      bid/ask must exist and be positive
   c. Notional check:   MAX_ESTIMATED_NOTIONAL (profile)
   d. Spread check:     OR(abs ≤ MAX_SPREAD (profile), pct ≤ OPTIONS_MAX_SPREAD_PCT (global))
4. Best accepted:       lowest spread_pct wins among all accepted candidates
```

Profile env vars (`PREVIEW_PROFILE_<X>_MAX_SPREAD`, `MIN_OPEN_INTEREST`, etc.) remain the primary levers for per-strategy liquidity tuning. The global settings fill in defaults and add logic that was previously missing (DTE window, relative spread, OI allowlist).

Current Render overrides keep strategy-type notional caps at 5000 so the paper system can test more otherwise-valid contracts without pausing a symbol cron:

```text
PREVIEW_PROFILE_MOVING_AVERAGE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_RSI_REVERSAL_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MACD_CROSSOVER_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MEAN_REVERSION_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_BREAKOUT_PRICE_THRESHOLD_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_VOLUME_CONFIRMED_BREAKOUT_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_VOLATILITY_SQUEEZE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_SUPPORT_RESISTANCE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_VWAP_RECLAIM_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_OPENING_RANGE_BREAKOUT_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_RELATIVE_STRENGTH_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_TIME_SERIES_MOMENTUM_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MARKET_REGIME_FILTER_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_PAIRS_RELATIVE_VALUE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_OPTIONS_SPREAD_CANDIDATE_MAX_ESTIMATED_NOTIONAL=5000
```

The May 18 tuning policy keeps SPY in the universe and treats SPY losses as evidence for scanner/profile tuning, not as an automatic reason to pause the SPY entry cron.

`options_spread_candidate` currently marks a signal as suitable for a spread, but contract preview and submission still use the single-leg long option pipeline. Do not treat it as true multi-leg spread execution until order-intent, preview, submit, and reconciliation support multi-leg orders.

## Diagnosing Rejections

When no contract passes, `option_selection_diagnostics` records a structured rejection summary grouped by reason:

```text
missing_open_interest    — OI field is null; not in allowlist
low_open_interest        — OI present but below MIN_OPEN_INTEREST
no_usable_two_sided_quote — bid or ask is zero or missing
missing_quote            — no quote returned at all
quote_unavailable        — Alpaca API error fetching quote
estimated_notional_above_max — ask × 100 × qty > MAX_ESTIMATED_NOTIONAL
spread_too_wide          — both absolute and relative spread thresholds exceeded
quote_size_too_low       — bid/ask size below MIN_QUOTE_SIZE
not_tradable             — contract status != active or tradable = false
no_expiration_strike_match — no contracts returned for the requested filters
```

Query for recent failures:

```sql
SELECT underlying_symbol, reason_counts, candidate_count, created_at
FROM option_selection_diagnostics
ORDER BY created_at DESC
LIMIT 50;
```

Query signals retired by preview attempts:

```sql
SELECT id, symbol, preview_attempts, last_preview_error_code, preview_rejection_reasons, last_previewed_at
FROM signals
WHERE status = 'preview_rejected'
ORDER BY last_previewed_at DESC
LIMIT 50;
```

Compact rejection summaries also appear in application logs at INFO level:

```text
Option contract selection failed: SPY call — 12 candidate(s) checked,
rejections: [missing_open_interest×3, spread_too_wide×9]
```

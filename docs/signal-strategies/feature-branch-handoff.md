# Signal strategies feature branch handoff

Branch: `feature/add-signal-strategies`

This file tracks what has been completed on the feature branch and what still needs to be done before the signal-strategy expansion can be considered complete.

## Completed on this feature branch

### Documentation

- README updated to reflect that `momentum_rate_of_change` is live in the scanner and no longer just a standalone evaluator.
- README updated to reflect that `moving_average` is evaluator-backed on this branch.
- README updated with a warning that `app/services/signal_scanner.py` is large and some GitHub connector reads have truncated it before the evaluator helper implementations.

### Evaluators implemented and registered

The reusable evaluator registry now includes:

```text
momentum_rate_of_change
moving_average
rsi_reversal
macd_crossover
mean_reversion
```

Implemented evaluator files:

```text
app/services/signals/evaluators/momentum.py
app/services/signals/evaluators/moving_average.py
app/services/signals/evaluators/rsi.py
app/services/signals/evaluators/macd.py
app/services/signals/evaluators/mean_reversion.py
```

Registry file:

```text
app/services/signals/evaluators/registry.py
```

### Evaluator feature flags added

Settings currently include:

```text
SIGNAL_EVALUATORS_ENABLED
MOMENTUM_EVALUATOR_ENABLED
MOVING_AVERAGE_EVALUATOR_ENABLED
RSI_EVALUATOR_ENABLED
MACD_EVALUATOR_ENABLED
MEAN_REVERSION_EVALUATOR_ENABLED
```

### Tests added

Evaluator tests added:

```text
tests/services/signals/test_moving_average_evaluator.py
tests/services/signals/test_rsi_evaluator.py
tests/services/signals/test_macd_evaluator.py
tests/services/signals/test_mean_reversion_evaluator.py
```

Scanner evaluator tests were extended for moving-average routing and feature-flag behavior:

```text
tests/services/signals/test_signal_scanner_evaluator.py
```

## Current live scanner-routing status

```text
momentum_rate_of_change: implemented, registered, scanner-routed
moving_average: implemented, registered, scanner-routed on this feature branch
rsi_reversal: implemented, registered, unit-tested, not scanner-routed yet
macd_crossover: implemented, registered, unit-tested, not scanner-routed yet
mean_reversion: implemented, registered, unit-tested, not scanner-routed yet
```

## Must-do before merging or considering the feature complete

### 1. Run the focused evaluator tests locally

```bash
python -m pytest tests/services/signals/test_moving_average_evaluator.py
python -m pytest tests/services/signals/test_rsi_evaluator.py
python -m pytest tests/services/signals/test_macd_evaluator.py
python -m pytest tests/services/signals/test_mean_reversion_evaluator.py
python -m pytest tests/services/signals/test_signal_scanner_evaluator.py
```

Then run the broader suite:

```bash
python -m pytest
```

### 2. Wire RSI, MACD, and mean reversion into `signal_scanner.py`

Edit locally or with full-file-safe access only.

Add scanner dispatch for:

```text
scanner.type == "rsi_reversal"
scanner.type == "macd_crossover"
scanner.type == "mean_reversion"
```

Use the same evaluator-backed scanner pattern that is already used for:

```text
scanner.type == "momentum_rate_of_change"
scanner.type == "moving_average"
```

Do not create a separate hand-built scanner path for these strategies unless there is a deliberate compatibility reason. The intended architecture is scanner config → evaluator registry → normalized `SignalCandidate` → scanner signal spec.

Feature-flag behavior should be consistent with the existing evaluator paths:

```text
SIGNAL_EVALUATORS_ENABLED=false should disable evaluator-backed scanners.
RSI_EVALUATOR_ENABLED=false should disable rsi_reversal.
MACD_EVALUATOR_ENABLED=false should disable macd_crossover.
MEAN_REVERSION_EVALUATOR_ENABLED=false should disable mean_reversion.
```

### 3. Add scanner-level tests for RSI, MACD, and mean reversion

Add or extend scanner tests to prove each scanner type can create a `Signal` through the evaluator-backed path:

```text
rsi_reversal creates bullish signal
rsi_reversal creates bearish signal or no-signal path
macd_crossover creates bullish signal
macd_crossover creates bearish signal or no-signal path
mean_reversion creates bullish lower-band recovery signal
mean_reversion creates bearish upper-band rejection signal or no-signal path
feature flag disabled returns no signals and records no-signal reason
missing bars returns no signals and records no-signal reason
evaluator returns None path records no-signal reason
```

### 4. Re-check compatibility with existing strategy configs

Before seeding or migrating production strategies, verify config names and defaults are compatible with existing patterns.

Important expected scanner config fields:

```text
symbols
timeframe
lookback_minutes
data_feed
dedupe_minutes
direction
signal_type
```

RSI-specific:

```text
rsi_period
oversold_level
overbought_level
confirmation_mode
require_price_confirmation
trend_average_window
trend_average_type
reject_trend_conflict
```

MACD-specific:

```text
fast_period
slow_period
signal_period
require_price_confirmation
require_histogram_confirmation
```

Mean-reversion-specific:

```text
bollinger_period
bollinger_stddev
require_price_confirmation
max_distance_to_middle_percent
```

### 5. Update strategy templates and seed script later

Once scanner routing is live and tests pass, update strategy templates and/or seed script to optionally seed the new strategy families.

Likely files:

```text
app/services/strategy_templates.py
scripts/seed_paper_trade_universe.py
scripts/update_strategy_preview_profiles.py
README.md
```

Add templates for:

```text
rsi_reversal
macd_crossover
mean_reversion
```

Do not seed all new strategy types live by default until paper testing confirms signal volume and quality.

### 6. Add preview profiles for new strategy types

Add env-backed preview profile support/documentation for:

```text
PAPER_PREVIEW_PROFILE_RSI_REVERSAL_*
PAPER_PREVIEW_PROFILE_MACD_CROSSOVER_*
PAPER_PREVIEW_PROFILE_MEAN_REVERSION_*
```

Keep existing `moving_average`, `momentum_rate_of_change`, `percent_change`, and `price_threshold` behavior compatible.

## Remaining documented strategies still not implemented

The following strategy families are still pending after this branch work:

```text
breakout_price_threshold
support_resistance
volume_confirmed_breakout
volatility_squeeze
```

Recommended order:

1. `breakout_price_threshold` / recent-range breakout
2. `volume_confirmed_breakout`
3. `volatility_squeeze`
4. `support_resistance`

Support/resistance should probably be last because it needs either manual levels, swing-point detection, or both.

## Important scanner-file warning

`app/services/signal_scanner.py` is large. During this work, GitHub connector reads repeatedly returned only a truncated first portion of the file, even though the file has more than 1,000 lines. Because GitHub file updates require full-file replacement, do not patch or replace `signal_scanner.py` unless you can view and preserve the complete file.

If a tool cannot show the complete file, stop and use a local checkout instead.

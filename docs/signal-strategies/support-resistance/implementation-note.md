# Support resistance implementation note

Branch: `feature/add-signal-strategies`

## Current status

`support_resistance` has been added as a reusable evaluator and is wired into the live scanner.

Implemented:

```text
app/services/signals/evaluators/support_resistance.py
tests/services/signals/test_support_resistance_evaluator.py
```

Registered:

```text
app/services/signals/evaluators/registry.py
```

Feature flag added:

```text
SUPPORT_RESISTANCE_EVALUATOR_ENABLED
```

## Supported evaluator behavior

The first implementation supports:

```text
manual support levels: support_levels
manual resistance levels: resistance_levels
recent swing support/resistance detection
swing_window
lookback_candles
min_touches
level_tolerance_percent
breakout_buffer_percent
max_distance_percent
mode = breakout, bounce, rejection, breakout_or_rejection, both
require_candle_confirmation
configured direction filtering
configured signal_type override
```

Default strategy type:

```text
support_resistance
```

Default signal types:

```text
resistance_breakout
support_breakdown
support_bounce
resistance_rejection
```

## Scanner routing

Scanner dispatch is implemented for:

```text
scanner.type == "support_resistance"
```

It uses the same evaluator-backed scanner pattern as:

```text
momentum_rate_of_change
moving_average
```

## Tests to run

```bash
python -m pytest tests/services/signals/test_support_resistance_evaluator.py
python -m pytest tests/services/signals/test_signal_scanner_evaluator.py
python -m pytest
```

Scanner-level tests should cover:

```text
manual resistance breakout creates bullish signal
manual support breakdown creates bearish signal
manual support bounce creates bullish signal
manual resistance rejection creates bearish signal
swing-level resistance breakout creates signal
feature flag disabled returns no signals and records no-signal reason
missing bars returns no signals and records no-signal reason
evaluator returns None path records no-signal reason
```

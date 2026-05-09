# Breakout price threshold implementation note

Branch: `feature/add-signal-strategies`

## Current status

`breakout_price_threshold` has been added as a reusable evaluator and is wired into the live scanner.

Implemented:

```text
app/services/signals/evaluators/breakout.py
tests/services/signals/test_breakout_evaluator.py
```

Registered:

```text
app/services/signals/evaluators/registry.py
```

Feature flag added:

```text
BREAKOUT_PRICE_THRESHOLD_EVALUATOR_ENABLED
```

## Supported evaluator behavior

The first implementation supports:

```text
configured bullish threshold: price_above
configured bearish threshold: price_below
recent range breakout: range_lookback_candles
breakout_buffer_percent
max_breakout_distance_percent
require_price_confirmation
configured direction filtering
configured signal_type override
```

Default strategy type:

```text
breakout_price_threshold
```

Default signal types:

```text
price_breakout
price_breakdown
```

## Scanner routing

Scanner dispatch is implemented for:

```text
scanner.type == "breakout_price_threshold"
```

It uses the same evaluator-backed scanner pattern as:

```text
momentum_rate_of_change
moving_average
```

## Tests to run

```bash
python -m pytest tests/services/signals/test_breakout_evaluator.py
python -m pytest tests/services/signals/test_signal_scanner_evaluator.py
python -m pytest
```

Scanner-level tests should cover:

```text
configured bullish breakout creates signal
configured bearish breakdown creates signal
recent range bullish breakout creates signal
recent range bearish breakdown creates signal
feature flag disabled returns no signals and records no-signal reason
missing bars returns no signals and records no-signal reason
evaluator returns None path records no-signal reason
```

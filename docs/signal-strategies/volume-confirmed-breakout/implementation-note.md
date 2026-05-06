# Volume confirmed breakout implementation note

Branch: `feature/add-signal-strategies`

## Current status

`volume_confirmed_breakout` has been added as a reusable evaluator but is not yet wired into the live scanner.

Implemented:

```text
app/services/signals/evaluators/volume_breakout.py
tests/services/signals/test_volume_breakout_evaluator.py
```

Registered:

```text
app/services/signals/evaluators/registry.py
```

Feature flag added:

```text
VOLUME_CONFIRMED_BREAKOUT_EVALUATOR_ENABLED
```

## Supported evaluator behavior

The first implementation supports:

```text
configured bullish threshold: price_above
configured bearish threshold: price_below
recent range breakout: range_lookback_candles
breakout_buffer_percent
max_breakout_distance_percent
volume_lookback_candles
min_relative_volume
min_bullish_close_position
max_bearish_close_position
require_candle_confirmation
configured direction filtering
configured signal_type override
```

Default strategy type:

```text
volume_confirmed_breakout
```

Default signal types:

```text
volume_confirmed_price_breakout
volume_confirmed_price_breakdown
```

## Scanner routing still needed

Add scanner dispatch for:

```text
scanner.type == "volume_confirmed_breakout"
```

Use the existing evaluator-backed scanner pattern already used by:

```text
momentum_rate_of_change
moving_average
```

Do not edit `app/services/signal_scanner.py` unless the full file can be viewed and preserved. During this feature work, GitHub connector reads repeatedly truncated that file.

## Tests to run

```bash
python -m pytest tests/services/signals/test_volume_breakout_evaluator.py
python -m pytest tests/services/signals/test_signal_scanner_evaluator.py
python -m pytest
```

After scanner routing is added, add scanner-level tests for:

```text
configured bullish volume breakout creates signal
configured bearish volume breakdown creates signal
recent range bullish volume breakout creates signal
recent range bearish volume breakdown creates signal
relative volume too low records no-signal reason
candle confirmation failed records no-signal reason
feature flag disabled returns no signals and records no-signal reason
missing bars returns no signals and records no-signal reason
evaluator returns None path records no-signal reason
```

# Volatility squeeze implementation note

Branch: `feature/add-signal-strategies`

## Current status

`volatility_squeeze` has been added as a reusable evaluator but is not yet wired into the live scanner.

Implemented:

```text
app/services/signals/evaluators/volatility_squeeze.py
tests/services/signals/test_volatility_squeeze_evaluator.py
```

Registered:

```text
app/services/signals/evaluators/registry.py
```

Feature flag added:

```text
VOLATILITY_SQUEEZE_EVALUATOR_ENABLED
```

## Supported evaluator behavior

The first implementation supports:

```text
Bollinger Band width compression detection
recent range breakout confirmation
bullish breakout above recent range high
bearish breakdown below recent range low
squeeze_lookback_candles
range_lookback_candles
breakout_buffer_percent
max_band_width_percent
compression_ratio_threshold
max_breakout_distance_percent
require_price_confirmation
configured direction filtering
configured signal_type override
```

Default strategy type:

```text
volatility_squeeze
```

Default signal types:

```text
volatility_squeeze_bullish_breakout
volatility_squeeze_bearish_breakdown
```

## Scanner routing still needed

Add scanner dispatch for:

```text
scanner.type == "volatility_squeeze"
```

Use the existing evaluator-backed scanner pattern already used by:

```text
momentum_rate_of_change
moving_average
```

Do not edit `app/services/signal_scanner.py` unless the full file can be viewed and preserved. During this feature work, GitHub connector reads repeatedly truncated that file.

## Tests to run

```bash
python -m pytest tests/services/signals/test_volatility_squeeze_evaluator.py
python -m pytest tests/services/signals/test_signal_scanner_evaluator.py
python -m pytest
```

After scanner routing is added, add scanner-level tests for:

```text
bullish squeeze breakout creates signal
bearish squeeze breakdown creates signal
compression not detected records no-signal reason
price confirmation failed records no-signal reason
feature flag disabled returns no signals and records no-signal reason
missing bars returns no signals and records no-signal reason
evaluator returns None path records no-signal reason
```

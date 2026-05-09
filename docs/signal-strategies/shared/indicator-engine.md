# Shared indicator engine deep dive

## Goal

Build all signal strategies on top of one fast, testable indicator/scanner foundation instead of each strategy recalculating candles and indicators independently.

The main performance goal is:

```text
Fetch candle data once per symbol/timeframe per cycle.
Normalize it once.
Compute shared indicators once.
Let strategies read from the shared feature set.
```

## Recommended architecture

```text
market cycle
  -> load active strategy configs
  -> group strategies by symbol + timeframe + required lookback
  -> fetch bars once per group
  -> build CandleFrame
  -> compute shared IndicatorFrame
  -> run strategy evaluators
  -> emit normalized signals
```

## Core data structures

### Candle

Use one normalized internal candle shape regardless of the broker/data vendor response.

```python
@dataclass(frozen=True, slots=True)
class Candle:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
```

For speed, calculations can convert Decimal values to floats inside indicator helpers, then convert final signal values back to strings/Decimals where persisted.

### CandleFrame

```python
@dataclass(frozen=True, slots=True)
class CandleFrame:
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]
    complete_through: datetime
```

Important requirements:

- Candles sorted ascending by timestamp.
- Exclude incomplete current candle unless a strategy explicitly allows intrabar logic.
- Enforce minimum candle count before strategy evaluation.
- Track whether data is stale.

### IndicatorFrame

```python
@dataclass(frozen=True, slots=True)
class IndicatorFrame:
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float | None]
    sma: dict[int, list[float | None]]
    ema: dict[int, list[float | None]]
    rsi: dict[int, list[float | None]]
    macd: dict[tuple[int, int, int], MacdSeries]
    bollinger: dict[tuple[int, float], BollingerSeries]
    atr: dict[int, list[float | None]]
    vwap: list[float | None]
```

Only compute the indicators needed by the active strategies in that cycle.

## Indicator calculation principles

### Avoid repeated recalculation

If 20 strategies need a 20 EMA for the same symbol/timeframe, compute it once.

Recommended interface:

```python
indicator_frame.ema(period=20)
indicator_frame.rsi(period=14)
indicator_frame.bollinger(period=20, stddev=2.0)
```

The method should memoize results within the frame.

### Keep pure functions

Indicator helpers should be pure and easy to unit test.

```python
def ema(values: Sequence[float], period: int) -> list[float | None]: ...
def rsi(values: Sequence[float], period: int) -> list[float | None]: ...
def macd(values: Sequence[float], fast: int, slow: int, signal: int) -> MacdSeries: ...
```

Do not let indicator helpers read environment variables, database rows, or broker clients.

### Require enough warmup candles

Indicators need warmup data. Do not evaluate a signal if the latest indicator value is `None`.

Suggested minimum candles:

```text
SMA/EMA: period + 2
RSI: period + 2
MACD: slow_period + signal_period + 5
Bollinger: period + 2
ATR: period + 2
Support/resistance swings: lookback + swing_window * 2
```

## Strategy evaluator interface

Each strategy should implement the same shape.

```python
class SignalEvaluator(Protocol):
    strategy_type: str

    def required_features(self, config: dict) -> RequiredFeatures:
        ...

    def evaluate(
        self,
        *,
        symbol: str,
        config: dict,
        candles: CandleFrame,
        indicators: IndicatorFrame,
        market_regime: MarketRegime | None,
    ) -> SignalCandidate | None:
        ...
```

## Normalized signal candidate

```python
@dataclass(frozen=True, slots=True)
class SignalCandidate:
    symbol: str
    strategy_type: str
    signal_type: str
    direction: Literal["bullish", "bearish"]
    confidence: Decimal
    rationale: str
    features: dict[str, Any]
    dedupe_key: str
```

The evaluator should not select option contracts. It should only describe the directional signal.

## Dedupe design

Duplicate signals currently suppress repeated entries. Keep this behavior but make dedupe keys predictable.

Recommended key:

```text
{symbol}:{strategy_type}:{signal_type}:{direction}:{time_bucket}
```

Time bucket can be rounded to `dedupe_minutes`.

Example:

```text
SPY:macd_crossover:macd_bullish_cross:bullish:2026-05-04T14:00
```

## Efficient cycle planning

Before fetching market data, inspect active strategies and build a fetch plan.

Example:

```python
@dataclass(frozen=True, slots=True)
class DataRequirement:
    symbol: str
    timeframe: str
    lookback_minutes: int
    indicators: set[IndicatorRequirement]
```

Then merge requirements by symbol/timeframe:

```text
SPY 5Min needs 1440 minutes for moving average
SPY 5Min needs 2880 minutes for MACD
=> fetch SPY 5Min once with 2880 minutes
```

## Error handling

Strategy evaluation should return skip reasons rather than raising for normal market conditions.

Examples:

```text
not_enough_candles
stale_candle_data
indicator_not_ready
market_regime_conflict
duplicate_signal
outside_trade_window
```

Raise exceptions only for true system failures.

## Testing strategy

Create fixture candle series for each common scenario:

- steady uptrend
- steady downtrend
- sideways chop
- breakout
- failed breakout
- squeeze then breakout
- mean reversion bounce
- RSI oversold recovery
- MACD bullish cross

Each evaluator should have tests for:

- bullish signal
- bearish signal
- no signal
- not enough data
- stale data
- dedupe key output
- confidence score boundaries

## Implemented rollout order

1. Build CandleFrame normalization.
2. Build shared indicator helpers and unit tests.
3. Build evaluator interface and registry.
4. Add evaluator-backed moving average, momentum rate of change, and breakout price threshold strategies.
5. Add RSI and MACD.
6. Add mean reversion.
7. Add support/resistance and volume breakout.
8. Add volatility squeeze.

## Performance target

The entry cron runs every 5 minutes. Evaluation should finish comfortably inside one cycle.

Target behavior:

```text
15 symbols x 5Min candles x multiple strategies
single data fetch plan
single indicator calculation per symbol/timeframe
all strategy evaluations in memory
```

Avoid database writes until a candidate signal passes all scanner-level checks.

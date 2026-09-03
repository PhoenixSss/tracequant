# TraceQuant Python package getting started

This guide covers the current `tracequant` bootstrap package. It provides
small, validated foundations for later research work; it does not provide a
trading system, executable strategy, exchange client, backtester, execution
service, risk engine, or Live runtime. See the [TraceQuant README](../../README.md)
for the project identity and current capability boundary.

## Configuration

Loading is explicit. The `environ` argument makes a deterministic isolated
mapping useful in tests; omitting it reads the process environment at call
time:

```python
from tracequant.config import Environment, Settings, load_settings

settings = load_settings(environ={"TRACEQUANT_ENV": "development"})
assert settings == Settings(environment=Environment.DEVELOPMENT)
```

Explicit arguments take precedence over environment values, which take
precedence over defaults. `TRACEQUANT_ENV` is required. Importing the config
module does not read environment values, load dotenv files, create directories,
or create a global settings object.

## Structured logging

Configure logging explicitly after loading settings:

```python
from tracequant.config import Environment, Settings
from tracequant.logging import configure_logging

settings = Settings(environment=Environment.DEVELOPMENT)
configure_logging(settings)
```

The current logging implementation emits one-line JSON records to stderr. It
can additionally append to `tracequant.jsonl` below an explicitly configured
directory. It only accepts `LogFormat.JSON`; although the configuration parser
accepts both `text` and `json` as values for `TRACEQUANT_LOG_FORMAT`, passing
`LogFormat.TEXT` to `configure_logging` raises `LoggingConfigError`. Use JSON
for the current logging path.

Known sensitive mapping keys are redacted case-insensitively, and
`SecretValue` is representation-safe. This is not encryption or a secret
manager, and redaction cannot reliably detect a credential manually
concatenated into a free-text message. Never put credentials in log messages.

## UTC time

All domain timestamps must be timezone-aware. Utilities accept aware non-UTC
values and normalize them to UTC; naive values are rejected:

```python
from datetime import UTC, datetime

from tracequant.core.time import format_utc, parse_utc

when = datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
assert format_utc(when) == "2024-02-29T23:45:00Z"
assert parse_utc("2024-02-29T23:45:00Z") == when
```

## Initial domain models

The public import path is:

```python
import json
from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar

bar = OHLCVBar(
    instrument=InstrumentId("BTCUSDT"),
    start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
    end=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    open=100.0,
    high=110.0,
    low=90.0,
    close=105.0,
    volume=12.5,
)
json.dumps(bar.to_dict(), allow_nan=False)
```

Models are immutable and validate their inputs. `InstrumentId` is a trimmed,
ASCII uppercase letters-and-digits identifier of at most 32 characters;
`TimeRange` is a UTC half-open interval; and `OHLCVBar` contains finite float
OHLCV values with non-negative volume and consistent high/low bounds. These
models intentionally do not represent venues, order books, orders, accounts,
timeframes, persistence schemas, or exchange metadata. See [Initial public
domain models](../architecture/domain-models.md) for the full boundary.

## Shared test fixtures

Reusable deterministic factories belong to `tests/fixtures/domain.py`, not to
the production package. `tests/conftest.py` exposes them as function-scoped
pytest fixtures:

```python
from fixtures.domain import BarFactory


def test_example_bar(bar_factory: BarFactory) -> None:
    bar = bar_factory()
    assert str(bar.instrument) == "BTCUSDT"
```

Use explicit symbols, UTC timestamps, and prices when those values matter to a
test. The fixed factory defaults are test conveniences and are not production
data or runtime configuration.

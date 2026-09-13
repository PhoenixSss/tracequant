# Nautilus BTCUSDT 1h historical bar capability

- **Evidence date:** 2026-09-13
- **Issue:** [#338](https://github.com/PhoenixSss/tracequant/issues/338)
- **Fixed runtime:** `nautilus-trader==2.0.0rc4` on CPython 3.13.14
- **Data path:** `USE_BINANCE_ARCHIVE`
- **Research Outcome:** IMPLEMENT

This record answers only whether TraceQuant can obtain BTCUSDT USDⓈ-M
perpetual 1h LAST closed bars for a fixed 30–90 day phase-1 window without
account credentials. It does not implement a downloader, catalog pipeline, or
backtest.

## Research Outcome

IMPLEMENT

Phase-1 implementation must use the Binance public historical archive, convert
rows to Nautilus `Bar`, and write `ParquetDataCatalog`. It must not depend on
Nautilus `request_bars` against Binance REST from this environment.

## Decision

`USE_BINANCE_ARCHIVE`. `USE_NAUTILUS` is rejected for phase 1 because the locked
Nautilus Binance data client cannot reach the public REST origin from the
current runtime. `NEEDS MORE EVIDENCE` is not used: the archive path was
executed against real objects, converted to native `Bar`, and round-tripped
through `ParquetDataCatalog`.

## Nautilus public REST path — blocked

No API key or secret was supplied. The locked public API was executed, not
mocked.

```text
uv run --frozen python -c "
import asyncio
from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig, BinanceEnvironment,
    BinanceInstrumentProviderConfig, BinanceProductType,
    load_binance_instruments,
)
config = BinanceDataClientConfig(
    product_type=BinanceProductType.USD_M,
    environment=BinanceEnvironment.LIVE,
    api_key=None, api_secret=None,
    instrument_provider=BinanceInstrumentProviderConfig(
        load_all=False, load_ids=['BTCUSDT-PERP.BINANCE'],
    ),
)
print(asyncio.run(load_binance_instruments(config)))
"
```

Result: `ValueError: Binance error 0: Service unavailable from a restricted
location according to 'b. Eligibility' in https://www.binance.com/en/terms.`

Direct public REST from the same host:

| Request | Result |
| --- | --- |
| `GET https://fapi.binance.com/fapi/v1/ping` | HTTP 451, same eligibility JSON |
| `GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2` | HTTP 451, same eligibility JSON |
| `GET https://api.binance.com/api/v3/ping` | HTTP 451 |

`request_bars` was therefore not a reachable experiment: instrument load and
kline REST fail before a LiveNode historical request can return bars. This is
runtime evidence of origin unavailability, not a missing Nautilus API. The
2.0.0rc4 surface still exposes `BinanceDataClientConfig`,
`BinanceDataClientFactory`, `load_binance_instruments`, and
`DataActor.request_bars`.

## Archive path — verified

`https://data.binance.vision/` is reachable without credentials. USD-M 1h kline
objects exist for native symbol `BTCUSDT`.

| Object | Result |
| --- | --- |
| Daily `BTCUSDT-1h-2026-09-10.zip` … `2026-09-12.zip` | HTTP 200; 24 closed bars each; 72 bars total |
| Monthly `BTCUSDT-1h-2026-08.zip` | HTTP 200; 744 bars (31 × 24) |
| Monthly HEAD `2026-05` … `2026-08` | HTTP 200; enough objects for a 90-day window |

CSV schema (header present):

```text
open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore
```

`open_time` is the UTC hour start in milliseconds. `close_time` is the last
millisecond of that hour (`HH:59:59.999`). Files contain only closed hours.

Conversion used Nautilus 2.0.0rc4 `Bar` with:

- `BarType`: `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL`
- `ts_event` / `ts_init`: `close_time_ms * 1_000_000`
- prices/volume: `Price.from_str` / `Quantity.from_str` of the CSV fields

That `ts_event` rule matches `parse_klines_to_binance_bars` in
`nautilus_trader` 2.0.0rc4 (`close_time` → bar event time). It was applied to
archive rows because live `request_bars` could not be observed.

| Check | 3-day daily files | 2026-08 monthly file |
| --- | --- | --- |
| Native type | `Bar` | `Bar` |
| Count | 72 | 744 |
| First `ts_event` | 2026-09-10T00:59:59.999Z | 2026-08-01T00:59:59.999Z |
| Last `ts_event` | 2026-09-12T23:59:59.999Z | 2026-08-31T23:59:59.999Z |
| Strictly increasing | yes | yes |
| Duplicate `ts_event` | 0 | 0 |
| Hourly gaps | 0 | 0 |

`ParquetDataCatalog.write_bars` then `query_bars` on the 72-bar sample:

| Field | Value |
| --- | --- |
| Wrote / read count | 72 / 72 |
| First/last match | 2026-09-10T00:59:59.999Z … 2026-09-12T23:59:59.999Z |
| Catalog relative file | `data/bars/BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL/2026-09-10T00-59-59-999000000Z_2026-09-12T23-59-59-999000000Z.parquet` |
| Read type | `Bar` |

A 30–90 day phase-1 window does not need Nautilus REST pagination. Daily zips
are 24 bars; monthly zips cover a calendar month. Three monthly objects plus
the current partial month of daily objects cover 90 days. No Binance REST
`limit` applies.

Reproduce the archive fetch (no credentials):

```text
curl -fsSL -A tracequant-research-338 \
  -O https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09-12.zip
curl -fsSIL -A tracequant-research-338 \
  https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-08.zip
```

## Inputs for a later implementation Task

| Input | Value |
| --- | --- |
| Origin | `https://data.binance.vision/` public archive; no API key |
| Product | Binance USDⓈ-M perpetual |
| Native symbol | `BTCUSDT` |
| Nautilus `InstrumentId` | `BTCUSDT-PERP.BINANCE` |
| `BarType` | `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL` |
| Daily URL | `/data/futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-YYYY-MM-DD.zip` |
| Monthly URL | `/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-YYYY-MM.zip` |
| Closed-bar timestamp | `ts_event = close_time_ms * 1e6`; interval open is `open_time` |
| Catalog write | `ParquetDataCatalog.write_bars` of core `Bar` objects |
| Out of scope | Nautilus LiveNode, `request_bars`, trades/funding/mark, other symbols/timeframes, account/Demo/Live execution |

## Confirmed limitations

- Nautilus instrument identity was not loaded live; `BTCUSDT-PERP.BINANCE` is
  the 2.0.0rc4 USD-M perpetual mapping from the public adapter contract.
- Price and size precision were taken from archive decimal strings, not from
  `exchangeInfo`.
- Archive objects lag the in-progress UTC day; phase-1 windows should use
  completed daily or monthly files.
- A different network that can reach `fapi.binance.com` might still use
  `request_bars`. That was not verified and is not the selected phase-1 path.
- This research does not ship fetch, conversion, or catalog code.

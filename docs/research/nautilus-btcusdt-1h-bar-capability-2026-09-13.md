# Nautilus BTCUSDT 1h historical bar capability

- **Evidence and correction date:** 2026-09-13
- **Correction Issue:** [#342](https://github.com/PhoenixSss/tracequant/issues/342)
- **Superseded phase-1 decision:** [#338](https://github.com/PhoenixSss/tracequant/issues/338)
- **Fixed runtime:** `nautilus-trader==2.0.0rc4` on CPython 3.13.14
- **Product and origin:** BTCUSDT USDⓈ-M perpetual from public
  `https://fapi.binance.com`
- **Credentials:** `api_key=None`, `api_secret=None`
- **Corrected data path:** `USE_NAUTILUS`
- **Research Outcome:** IMPLEMENT

This record corrects the phase-1 data-path decision in #338. It answers only
whether TraceQuant can obtain fixed 30–90 day windows of BTCUSDT USDⓈ-M
perpetual 1h LAST closed bars through the locked Nautilus Binance data client
without account credentials. It does not implement a downloader, production
catalog pipeline, scheduler, gap repair, strategy, or backtest.

## Research Outcome

IMPLEMENT

The unique corrected decision is **`USE_NAUTILUS`**. With the runtime proxy
explicitly supplied to `BinanceDataClientConfig.proxy_url`, the locked rc4
client loaded the instrument, returned native `Bar` objects for bounded REST
requests, covered a fixed 90-day window through explicit time segmentation,
and round-tripped both the short and 90-day results through
`ParquetDataCatalog`.

`USE_BINANCE_ARCHIVE` is no longer selected. The archive evidence from #338
remains valid as fallback research, but the only evidence previously used to
reject Nautilus was an HTTP 451 observed with the required proxy absent.
`NEEDS MORE EVIDENCE` is unnecessary because every #342 evaluation criterion
was exercised against the public origin and real rc4 objects.

## Why the original decision is invalid

#338 ran with the proxy missing from the effective Nautilus configuration and
observed the following real results:

| Historical #338 request | Historical result |
| --- | --- |
| Nautilus public instrument load | `ValueError: Binance error 0: Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms.` |
| `GET https://fapi.binance.com/fapi/v1/ping` | HTTP 451, eligibility JSON |
| `GET .../fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2` | HTTP 451, eligibility JSON |
| `GET https://api.binance.com/api/v3/ping` | HTTP 451 |

Those observations are preserved; they are not rewritten as successful
requests. They demonstrate the behavior of the wrong network exit only. On
2026-09-13 the runtime was corrected, the proxy was explicitly passed to
`BinanceDataClientConfig.proxy_url`, and the same public USDⓈ-M REST origin
became reachable. The historical 451 therefore cannot remain the deciding
evidence for the phase-1 path.

## Secure proxy and public REST verification

Only the presence of a standard HTTPS proxy environment variable and
`config.has_proxy_url == True` were printed. The proxy address, credentials,
headers, and complete environment were neither recorded nor written to this
repository.

The corrected public REST checks returned:

| Request | Actual result |
| --- | --- |
| `GET https://fapi.binance.com/fapi/v1/ping` | HTTP 200, `{}` |
| `GET https://fapi.binance.com/fapi/v1/time` | HTTP 200, `serverTime=1789302982730` (`2026-09-13T12:36:22.730Z`) |

Minimal safe configuration used for both instrument loading and the live data
client:

```python
proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
assert proxy_url

config = BinanceDataClientConfig(
    product_type=BinanceProductType.USD_M,
    environment=BinanceEnvironment.LIVE,
    api_key=None,
    api_secret=None,
    proxy_url=proxy_url,
    instrument_provider=BinanceInstrumentProviderConfig(
        load_all=False,
        load_ids=["BTCUSDT-PERP.BINANCE"],
    ),
)
assert config.has_proxy_url
```

The proxy value must remain a runtime secret. A deployment may inject it, but
#339 must not log, persist, commit, or accept it as a repository default.

## Instrument identity and exchange precision

`load_binance_instruments(config)` executed against the public USDⓈ-M
`exchangeInfo` path and returned exactly one instrument:

| Field | Actual value |
| --- | --- |
| Native Nautilus type | `CryptoPerpetual` |
| `InstrumentId` | `BTCUSDT-PERP.BINANCE` |
| Price precision | 2 |
| Size precision | 3 |
| Price increment | `0.10` |
| Size increment | `0.001` |

No key or secret was supplied for this load.

## Native Nautilus short-window request

The configured client was registered with `BinanceDataClientFactory` in an rc4
`LiveNode`. A `DataActor` made the following historical request; curl, archive,
mock, and fixture data were not substituted:

```python
bar_type = BarType.from_str(
    "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL",
)
self.request_bars(
    bar_type=bar_type,
    start=datetime(2026, 9, 10, tzinfo=UTC),
    end=datetime(2026, 9, 13, tzinfo=UTC),
    client_id=BINANCE_CLIENT_ID,
)
```

The node used a 120-second connection timeout because the proxy-routed initial
instrument load exceeded the default 30-second node timeout in one trial. Once
connected, both public Binance WebSockets and the historical REST request used
the configured proxy successfully.

| Check | Actual result |
| --- | --- |
| Requested interval | `[2026-09-10T00:00:00Z, 2026-09-13T00:00:00Z)` |
| Count | 72 |
| Native type | `Bar` |
| `BarType` | `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL` |
| First `ts_event` | `2026-09-10T00:59:59.999000000Z` |
| Last `ts_event` | `2026-09-12T23:59:59.999000000Z` |
| Strictly increasing | yes |
| Duplicate `ts_event` | 0 |
| Non-hourly gaps | 0 |

## Closed-bar timestamp semantics

A raw public kline request over the same short window returned 72 rows. Its
first row had `open_time=1788998400000` and
`close_time=1789001999999`; the last had
`open_time=1789254000000` and `close_time=1789257599999`.
The corresponding Nautilus first and last `ts_event` values were exactly the
raw `close_time` values converted from milliseconds to nanoseconds.

The locked rc4 source corroborates the live observation:

- [`request_binance_bars`](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/futures/http/client.rs#L2804-L2858)
  passes the explicit bounds and limit to the kline endpoint and discards a bar
  whose close is not before the client clock;
- [`parse_futures_kline_binance_bar`](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/futures/http/client.rs#L3036-L3098)
  assigns `ts_event` from the raw kline `close_time`.

For #339, a bar is therefore identified by its hour-open boundary but is event
timestamped at the last millisecond of the completed hour. The observed request
contract is half-open `[start, end)`: an `end` at midnight returned the final
bar of the preceding UTC day.

## 1500-bar limit and fixed 90-day coverage

The [Binance USDⓈ-M kline API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
documents `limit` with a maximum of 1500. The public origin was also exercised
directly: `limit=1500` returned 1500 rows and `limit=1501` returned HTTP 400
with code `-1130` (`limit` invalid).

The locked rc4 implementation performs exactly one `klines` call for one
`request_bars`; it passes the caller's `start`, `end`, and `limit` directly and
contains no pagination loop. The practical result was confirmed by requesting
the whole 90-day range with `limit=1500`:

| Request | Count | First `ts_event` | Last `ts_event` |
| --- | ---: | --- | --- |
| `[2026-06-15, 2026-09-13)`, `limit=1500` | 1500 | `2026-06-15T00:59:59.999Z` | `2026-08-16T11:59:59.999Z` |

That one request does not cover the required 2160 hours. The same rc4
`request_bars` path was then called with three continuous, mutually exclusive
30-day windows, each below the server maximum:

| Segment | UTC half-open interval | Count | First / last `ts_event` |
| --- | --- | ---: | --- |
| 1 | `[2026-06-15, 2026-07-15)` | 720 | `2026-06-15T00:59:59.999Z` / `2026-07-14T23:59:59.999Z` |
| 2 | `[2026-07-15, 2026-08-14)` | 720 | `2026-07-15T00:59:59.999Z` / `2026-08-13T23:59:59.999Z` |
| 3 | `[2026-08-14, 2026-09-13)` | 720 | `2026-08-14T00:59:59.999Z` / `2026-09-12T23:59:59.999Z` |

Merged result:

| Check | Actual result |
| --- | --- |
| Count | 2160 (`90 × 24`) |
| Native type | `Bar` |
| `BarType` | `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL` |
| Strictly increasing | yes |
| Duplicate `ts_event` | 0 |
| Non-hourly gaps | 0 |

The implementation contract is explicit segmentation, not implicit rc4
pagination. Use adjacent half-open UTC windows with every request capped at
1500 bars or fewer, then reject any merged result with a duplicate, reversed
timestamp, or interval-sized gap.

## `ParquetDataCatalog` round-trip

Both native-Bar sets were written with `ParquetDataCatalog.write_bars` and read
back with `query_bars` from isolated temporary catalogs:

| Check | 3-day window | Segmented 90-day window |
| --- | ---: | ---: |
| Wrote / read | 72 / 72 | 2160 / 2160 |
| Read type | `Bar` | `Bar` |
| `BarType` preserved | yes | yes |
| First/last `ts_event` preserved | yes | yes |
| Relative parquet range | `2026-09-10T00-59-59-999000000Z_2026-09-12T23-59-59-999000000Z.parquet` | `2026-06-15T00-59-59-999000000Z_2026-09-12T23-59-59-999000000Z.parquet` |

The containing catalog path was temporary and is intentionally not recorded;
only the non-sensitive relative data range is shown.

## Reproduction outline

1. Use the repository lock with `uv run --frozen`; confirm
   `nautilus_trader.__version__ == "2.0.0rc4"`.
2. Read the existing HTTPS proxy from the runtime without printing it, pass it
   explicitly as `proxy_url`, and assert `config.has_proxy_url`.
3. Use `load_all=False` and
   `load_ids=["BTCUSDT-PERP.BINANCE"]`; keep both credentials `None`.
4. Register `BinanceDataClientFactory()` with an rc4 `LiveNode`, allow a
   120-second connection timeout for this proxy-routed environment, and issue
   the short request above from a `DataActor`.
5. Issue the 90-day capped request and the three half-open segment requests
   sequentially through the same actor. Inspect the returned objects, bar type,
   event-time order, duplicates, and exact one-hour deltas.
6. Write the 72-bar and merged 2160-bar lists into separate temporary
   `ParquetDataCatalog` instances and query them back.

Public endpoint reachability can be checked without printing proxy data:

```text
curl --silent --show-error --write-out '\nHTTP %{http_code}\n' \
  https://fapi.binance.com/fapi/v1/ping
curl --silent --show-error --write-out '\nHTTP %{http_code}\n' \
  https://fapi.binance.com/fapi/v1/time
```

These curl checks are connectivity diagnostics only; the decision depends on
the subsequent real Nautilus `request_bars` and catalog results.

## Preserved #338 archive evidence — not selected

The following #338 results remain valid and are retained as fallback evidence;
they are not evidence against the now-successful Nautilus path:

| Historical #338 archive check | Preserved result |
| --- | --- |
| `data.binance.vision` USDⓈ-M daily zips, 2026-09-10 through 2026-09-12 | HTTP 200; 24 closed bars each; 72 total |
| Monthly `BTCUSDT-1h-2026-08.zip` | HTTP 200; 744 bars (`31 × 24`) |
| Monthly objects 2026-05 through 2026-08 | HTTP 200; enough objects for the investigated 90-day window |
| Archive conversion | Native rc4 `Bar`, `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL`, raw `close_time` used as `ts_event` |
| Archive ordering checks | Strictly increasing; zero duplicate event times; zero hourly gaps |
| Archive catalog round-trip | 72 bars written and 72 read; type, bar type, first event, and last event preserved |

Archive daily files lag an in-progress UTC day, and monthly files cover only a
completed calendar month. Those limitations also remain preserved. The
archive should be reconsidered only through a future explicit decision if the
proxy-backed Nautilus path ceases to meet its contract; #342 does not implement
an automatic or multi-source fallback.

## Minimum input contract for #339

| Input | Required value or behavior |
| --- | --- |
| Data source | Nautilus Binance data client → public USDⓈ-M REST |
| Credentials | None; do not introduce account/API credentials |
| Proxy | Required runtime input to `BinanceDataClientConfig.proxy_url`; secret and never logged |
| Instrument | `BTCUSDT-PERP.BINANCE` |
| Bar type | `BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL` |
| Window convention | Adjacent UTC `[start, end)` intervals |
| Server/request cap | At most 1500 bars per request; do not assume rc4 pagination |
| Recommended fixed segmentation | 30-day windows (720 hourly bars) |
| Closed-bar event time | Raw kline `close_time` in ms → `ts_event` in ns |
| Completeness checks | Expected count, strict order, no duplicate event times, every delta exactly one hour |
| Persistence | `ParquetDataCatalog.write_bars` / `query_bars` on native `Bar` objects |

## Confirmed limitations

- This result depends on a proxy route that can carry both Binance public REST
  and public WebSocket traffic. Direct egress from the original #338 runtime
  remains subject to the preserved HTTP 451 observation.
- The first proxy-routed node connection exceeded the default 30-second
  connection timeout once. A 120-second timeout succeeded; #339 should expose
  an appropriate bounded deployment setting rather than assuming instant
  startup.
- rc4 does not auto-page a historical bar request. Correct segmentation,
  completeness checks, and retries remain implementation responsibilities.
- The test covered BTCUSDT USDⓈ-M perpetual 1h LAST external bars only. It does
  not approve other symbols, periods, price types, trades, funding, mark/index
  data, account APIs, Demo/Live trading, or execution.
- The already-verified Binance archive remains a possible future fallback, but
  #342 does not select or implement it.
- This research ships no production data-path code; that remains #339 scope.

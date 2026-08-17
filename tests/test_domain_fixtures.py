from fixtures.domain import make_invalid_ohlcv_payload, make_ohlcv_bar

from tracequant.domain import OHLCVBar


def test_factory_returns_equal_but_distinct_objects() -> None:
    first = make_ohlcv_bar()
    second = make_ohlcv_bar()

    assert first == second
    assert first is not second
    assert first.instrument is not second.instrument


def test_payload_factory_does_not_share_mutable_state() -> None:
    first = make_invalid_ohlcv_payload()
    second = make_invalid_ohlcv_payload()
    first["volume"] = 10.0

    assert second["volume"] == -1.0


def test_shared_fixture_is_function_scoped_and_fresh(sample_bar: OHLCVBar) -> None:
    assert sample_bar == make_ohlcv_bar()
    assert sample_bar is not make_ohlcv_bar()

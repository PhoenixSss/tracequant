import json

from quant_system.domain import OHLCVBar, TimeRange


def test_shared_time_range_fixture_is_function_scoped(
    sample_time_range: TimeRange,
) -> None:
    payload = sample_time_range.to_dict()
    payload["start"] = "2026-01-01T00:00:00Z"

    assert sample_time_range.to_dict()["start"] == "2026-02-28T23:45:00Z"


def test_shared_ohlcv_bar_fixture_is_reusable_and_json_ready(
    sample_ohlcv_bar: OHLCVBar,
) -> None:
    payload = sample_ohlcv_bar.to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert OHLCVBar.from_dict(payload) == sample_ohlcv_bar

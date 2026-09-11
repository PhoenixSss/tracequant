"""Private process boundary for one bounded Binance archive HTTP response."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tracequant.data.binance_public_archive import _urllib_http_get


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 5:
        return 2
    url, timeout_text, maximum_response_bytes_text, body_text, result_text = arguments
    body_path = Path(body_text)
    result_path = Path(result_text)
    try:
        maximum_response_bytes = (
            None
            if maximum_response_bytes_text == "none"
            else int(maximum_response_bytes_text)
        )
        response = _urllib_http_get(
            url,
            float(timeout_text),
            maximum_response_bytes=maximum_response_bytes,
        )
        body_path.write_bytes(response.body)
        result: dict[str, object] = {
            "kind": "response",
            "status": response.status,
            "headers": dict(response.headers),
            "complete": response.complete,
        }
    except BaseException as error:
        result = {
            "kind": "failure",
            "error_type": type(error).__name__,
            "detail": str(error)[:4096],
        }
    result_path.write_text(
        json.dumps(result, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

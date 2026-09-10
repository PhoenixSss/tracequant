"""Private cancellable process boundary for the default Binance REST transport."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Sequence

from tracequant.data.binance_kline_rest import _urllib_http_get


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 3:
        return 2
    url, timeout_text, maximum_response_bytes_text = arguments
    try:
        timeout = float(timeout_text)
        maximum_response_bytes = int(maximum_response_bytes_text)
        response = _urllib_http_get(url, timeout, maximum_response_bytes)
        payload: dict[str, object] = {
            "kind": "response",
            "status": response.status,
            "body_base64": base64.b64encode(response.body).decode("ascii"),
            "headers": dict(response.headers),
            "complete": response.complete,
        }
    except BaseException as error:
        payload = {
            "kind": "failure",
            "error_type": type(error).__name__,
            "detail": str(error)[:4096],
        }
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

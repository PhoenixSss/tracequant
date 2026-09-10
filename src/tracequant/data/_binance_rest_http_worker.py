"""Private cancellable process boundary for the default Binance REST transport."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Mapping, Sequence

from tracequant.data.binance_kline_rest import _urllib_http_get


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 3:
        return 2
    url, timeout_text, maximum_response_bytes_text = arguments

    def write_payload(payload: dict[str, object]) -> None:
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        sys.stdout.write("\n")
        sys.stdout.flush()

    def report_progress(
        kind: str, status: int, headers: Mapping[str, str], body: bytes
    ) -> None:
        if kind == "start":
            write_payload(
                {
                    "kind": "response-start",
                    "status": status,
                    "headers": dict(headers),
                }
            )
            return
        write_payload(
            {
                "kind": "response-body",
                "body_base64": base64.b64encode(body).decode("ascii"),
            }
        )

    try:
        timeout = float(timeout_text)
        maximum_response_bytes = int(maximum_response_bytes_text)
        response = _urllib_http_get(
            url,
            timeout,
            maximum_response_bytes,
            report_progress,
        )
        payload: dict[str, object] = {
            "kind": "response-complete",
            "complete": response.complete,
        }
    except BaseException as error:
        payload = {
            "kind": "failure",
            "error_type": type(error).__name__,
            "detail": str(error)[:4096],
        }
    write_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

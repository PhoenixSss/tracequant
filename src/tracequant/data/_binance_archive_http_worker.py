"""Private process boundary for one bounded Binance archive HTTP response."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from tracequant.data.binance_public_archive import _urllib_http_get


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 6:
        return 2
    (
        url,
        timeout_text,
        maximum_response_bytes_text,
        body_text,
        metadata_text,
        result_text,
    ) = arguments
    body_path = Path(body_text)
    metadata_path = Path(metadata_text)
    result_path = Path(result_text)
    body_stream: BinaryIO | None = None

    def report_progress(
        kind: str, status: int, headers: Mapping[str, str], body: bytes
    ) -> None:
        nonlocal body_stream
        if kind == "start":
            _write_json(
                metadata_path,
                {"kind": "response", "status": status, "headers": dict(headers)},
            )
            body_stream = body_path.open("wb")
            return
        if body_stream is None:
            raise RuntimeError("archive response body preceded response metadata")
        body_stream.write(body)
        body_stream.flush()

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
            progress=report_progress,
        )
        if body_stream is not None:
            body_stream.close()
            body_stream = None
        result: dict[str, object] = {
            "kind": "response",
            "status": response.status,
            "headers": dict(response.headers),
            "complete": response.complete,
        }
    except BaseException as error:
        if body_stream is not None:
            body_stream.close()
        result = {
            "kind": "failure",
            "error_type": type(error).__name__,
            "detail": str(error)[:4096],
        }
    _write_json(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

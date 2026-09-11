"""Finite execution context shared by Binance public-history adapters.

The context is deliberately provider- and adapter-specific.  It coordinates the
existing Binance archive and REST executors without deciding which source to use
or orchestrating fallback between them.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Never

__all__ = [
    "BinancePublicHistoryExecutionContext",
    "BinancePublicHistoryExecutionLimits",
    "BinancePublicHistoryExecutionSnapshot",
    "BinancePublicHistoryExecutionStopReason",
    "BinancePublicHistoryExecutionStopped",
    "BinancePublicHistoryHttpAllowance",
    "BinancePublicHistoryRequestAttempts",
]


class BinancePublicHistoryExecutionStopReason(StrEnum):
    """Stable reasons why the shared execution boundary refused more work."""

    CANCELLED = "cancelled"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    HTTP_ATTEMPTS_EXHAUSTED = "http_attempts_exhausted"
    RESPONSE_BYTES_EXHAUSTED = "response_bytes_exhausted"
    TOTAL_RESPONSE_BYTES_EXHAUSTED = "total_response_bytes_exhausted"
    ARCHIVE_OBJECTS_EXHAUSTED = "archive_objects_exhausted"
    REST_PAGES_EXHAUSTED = "rest_pages_exhausted"
    REQUEST_ATTEMPTS_EXHAUSTED = "request_attempts_exhausted"


class BinancePublicHistoryExecutionStopped(RuntimeError):
    """Internal control signal translated to an adapter result at public entries."""

    def __init__(
        self, reason: BinancePublicHistoryExecutionStopReason, detail: str
    ) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryExecutionLimits:
    """Immutable finite limits for one composable Binance execution ledger."""

    deadline_monotonic: float
    maximum_http_attempts: int
    maximum_total_response_bytes: int
    maximum_response_bytes: int
    maximum_archive_objects: int
    maximum_rest_pages: int
    maximum_attempts_per_request: int

    def __post_init__(self) -> None:
        if not isinstance(self.deadline_monotonic, (int, float)) or isinstance(
            self.deadline_monotonic, bool
        ):
            raise TypeError("deadline_monotonic must be a number")
        if not math.isfinite(self.deadline_monotonic):
            raise ValueError("deadline_monotonic must be finite")
        object.__setattr__(self, "deadline_monotonic", float(self.deadline_monotonic))
        for field in (
            "maximum_http_attempts",
            "maximum_total_response_bytes",
            "maximum_response_bytes",
            "maximum_archive_objects",
            "maximum_rest_pages",
            "maximum_attempts_per_request",
        ):
            value = getattr(self, field)
            if type(value) is not int:
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be greater than zero")


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryRequestAttempts:
    """Attempt count for one stable semantic archive resource or REST page."""

    request_key: str
    attempts: int


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryExecutionSnapshot:
    """Immutable, auditable view of the current shared ledger."""

    deadline_monotonic: float
    observed_monotonic: float
    cancelled: bool
    http_attempts: int
    response_bytes: int
    archive_objects: int
    rest_pages: int
    request_attempts: tuple[BinancePublicHistoryRequestAttempts, ...]
    stop_reason: BinancePublicHistoryExecutionStopReason | None

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - self.observed_monotonic)


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryHttpAllowance:
    """Bounded allowance reserved immediately before a real HTTP attempt."""

    timeout_seconds: float
    maximum_response_bytes: int
    attempt_number: int
    request_attempt_number: int


class BinancePublicHistoryExecutionContext:
    """Thread-safe finite ledger shared across archive and REST calls."""

    def __init__(
        self,
        limits: BinancePublicHistoryExecutionLimits,
        *,
        cancelled: Callable[[], bool] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(limits, BinancePublicHistoryExecutionLimits):
            raise TypeError("limits must be BinancePublicHistoryExecutionLimits")
        if cancelled is not None and not callable(cancelled):
            raise TypeError("cancelled must be callable")
        if monotonic_clock is not None and not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        if sleeper is not None and not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._limits = limits
        self._cancelled = cancelled or (lambda: False)
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._attempt_condition = threading.Condition(self._lock)
        self._http_attempts = 0
        self._response_bytes = 0
        self._reserved_response_bytes = 0
        self._response_byte_reservations: dict[int, int] = {}
        self._archive_objects = 0
        self._rest_pages = 0
        self._request_attempts: dict[str, int] = {}
        self._stop_reason: BinancePublicHistoryExecutionStopReason | None = None

    @property
    def limits(self) -> BinancePublicHistoryExecutionLimits:
        return self._limits

    def _now(self) -> float:
        value = self._monotonic_clock()
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("monotonic_clock must return a number")
        if not math.isfinite(value):
            raise ValueError("monotonic_clock must return a finite value")
        return float(value)

    def cancellation_requested(self) -> bool:
        value = self._cancelled()
        if not isinstance(value, bool):
            raise TypeError("cancelled must return a bool")
        return value

    def _stop(
        self, reason: BinancePublicHistoryExecutionStopReason, detail: str
    ) -> BinancePublicHistoryExecutionStopped:
        self._stop_reason = reason
        return BinancePublicHistoryExecutionStopped(reason, detail)

    def _check_control_locked(self, now: float, cancelled: bool) -> None:
        if cancelled:
            raise self._stop(
                BinancePublicHistoryExecutionStopReason.CANCELLED,
                "Binance public-history execution was cancelled",
            )
        if now >= self._limits.deadline_monotonic:
            raise self._stop(
                BinancePublicHistoryExecutionStopReason.DEADLINE_EXHAUSTED,
                "Binance public-history absolute deadline was exhausted",
            )

    def check(self) -> None:
        cancelled = self.cancellation_requested()
        now = self._now()
        with self._lock:
            self._check_control_locked(now, cancelled)

    def raise_stop(
        self, reason: BinancePublicHistoryExecutionStopReason, detail: str
    ) -> Never:
        """Record and raise a terminal stop discovered by an adapter boundary."""
        if not isinstance(reason, BinancePublicHistoryExecutionStopReason):
            raise TypeError("reason must be BinancePublicHistoryExecutionStopReason")
        if not isinstance(detail, str) or not detail:
            raise ValueError("detail must be a non-empty string")
        with self._lock:
            raise self._stop(reason, detail)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._limits.deadline_monotonic - self._now())

    def begin_archive_object(self) -> None:
        cancelled = self.cancellation_requested()
        now = self._now()
        with self._lock:
            self._check_control_locked(now, cancelled)
            if self._archive_objects >= self._limits.maximum_archive_objects:
                raise self._stop(
                    BinancePublicHistoryExecutionStopReason.ARCHIVE_OBJECTS_EXHAUSTED,
                    "maximum_archive_objects exhausted",
                )
            self._archive_objects += 1

    def begin_rest_page(self) -> None:
        cancelled = self.cancellation_requested()
        now = self._now()
        with self._lock:
            self._check_control_locked(now, cancelled)
            if self._rest_pages >= self._limits.maximum_rest_pages:
                raise self._stop(
                    BinancePublicHistoryExecutionStopReason.REST_PAGES_EXHAUSTED,
                    "maximum_rest_pages exhausted",
                )
            self._rest_pages += 1

    def begin_http_attempt(
        self, request_key: str, *, timeout_seconds: float
    ) -> BinancePublicHistoryHttpAllowance:
        """Reserve counters immediately before entering the transport callable."""
        if not isinstance(request_key, str) or not request_key:
            raise ValueError("request_key must be a non-empty string")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(
            timeout_seconds, bool
        ):
            raise TypeError("timeout_seconds must be a number")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and greater than zero")
        while True:
            cancelled = self.cancellation_requested()
            now = self._now()
            with self._attempt_condition:
                self._check_control_locked(now, cancelled)
                if self._response_byte_reservations:
                    self._attempt_condition.wait(
                        timeout=min(0.05, self._limits.deadline_monotonic - now)
                    )
                    continue
                if self._http_attempts >= self._limits.maximum_http_attempts:
                    raise self._stop(
                        BinancePublicHistoryExecutionStopReason.HTTP_ATTEMPTS_EXHAUSTED,
                        "maximum_http_attempts exhausted",
                    )
                request_attempts = self._request_attempts.get(request_key, 0)
                if request_attempts >= self._limits.maximum_attempts_per_request:
                    raise self._stop(
                        BinancePublicHistoryExecutionStopReason.REQUEST_ATTEMPTS_EXHAUSTED,
                        "maximum_attempts_per_request exhausted",
                    )
                remaining_bytes = (
                    self._limits.maximum_total_response_bytes
                    - self._response_bytes
                    - self._reserved_response_bytes
                )
                if remaining_bytes <= 0:
                    raise self._stop(
                        BinancePublicHistoryExecutionStopReason.TOTAL_RESPONSE_BYTES_EXHAUSTED,
                        "maximum_total_response_bytes exhausted",
                    )
                self._http_attempts += 1
                request_attempts += 1
                self._request_attempts[request_key] = request_attempts
                allowance = BinancePublicHistoryHttpAllowance(
                    timeout_seconds=min(
                        float(timeout_seconds), self._limits.deadline_monotonic - now
                    ),
                    maximum_response_bytes=min(
                        self._limits.maximum_response_bytes, remaining_bytes
                    ),
                    attempt_number=self._http_attempts,
                    request_attempt_number=request_attempts,
                )
                self._reserved_response_bytes += allowance.maximum_response_bytes
                self._response_byte_reservations[allowance.attempt_number] = (
                    allowance.maximum_response_bytes
                )
                return allowance

    def record_response_bytes(
        self, allowance: BinancePublicHistoryHttpAllowance, amount: int
    ) -> None:
        """Settle one attempt's reservation with bytes actually returned/read."""
        if not isinstance(allowance, BinancePublicHistoryHttpAllowance):
            raise TypeError("allowance must be BinancePublicHistoryHttpAllowance")
        if type(amount) is not int:
            raise TypeError("amount must be an integer")
        if amount < 0:
            raise ValueError("amount must not be negative")
        with self._attempt_condition:
            reserved = self._response_byte_reservations.pop(
                allowance.attempt_number, None
            )
            if reserved is None or reserved != allowance.maximum_response_bytes:
                raise ValueError("allowance is not an outstanding reservation")
            self._reserved_response_bytes -= reserved
            self._attempt_condition.notify_all()
            self._response_bytes += amount
            if amount > self._limits.maximum_response_bytes:
                raise self._stop(
                    BinancePublicHistoryExecutionStopReason.RESPONSE_BYTES_EXHAUSTED,
                    "maximum_response_bytes exceeded",
                )
            if self._response_bytes > self._limits.maximum_total_response_bytes:
                raise self._stop(
                    BinancePublicHistoryExecutionStopReason.TOTAL_RESPONSE_BYTES_EXHAUSTED,
                    "maximum_total_response_bytes exceeded",
                )

    def wait_for_retry(self, seconds: float) -> None:
        """Wait in bounded slices so deadline and cancellation remain observable."""
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise TypeError("seconds must be a number")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seconds must be finite and non-negative")
        self.check()
        if seconds > self.remaining_seconds:
            with self._lock:
                raise self._stop(
                    BinancePublicHistoryExecutionStopReason.DEADLINE_EXHAUSTED,
                    "retry wait exceeds remaining absolute deadline",
                )
        remaining = float(seconds)
        while remaining > 0:
            self.check()
            interval = min(0.05, remaining)
            self._sleeper(interval)
            remaining -= interval
        self.check()

    def snapshot(self) -> BinancePublicHistoryExecutionSnapshot:
        cancelled = self.cancellation_requested()
        now = self._now()
        with self._lock:
            return BinancePublicHistoryExecutionSnapshot(
                deadline_monotonic=self._limits.deadline_monotonic,
                observed_monotonic=now,
                cancelled=cancelled,
                http_attempts=self._http_attempts,
                response_bytes=self._response_bytes,
                archive_objects=self._archive_objects,
                rest_pages=self._rest_pages,
                request_attempts=tuple(
                    BinancePublicHistoryRequestAttempts(key, attempts)
                    for key, attempts in sorted(self._request_attempts.items())
                ),
                stop_reason=self._stop_reason,
            )

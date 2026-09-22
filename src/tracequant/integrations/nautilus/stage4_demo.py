"""Offline admission contracts for the Stage 4 Binance Demo boundary."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from importlib.metadata import distribution
from pathlib import Path
from typing import Final, cast

from nautilus_trader.adapters.binance import (
    BinanceEnvironment,
    BinanceExecutionClientConfig,
    BinanceMarginType,
    BinanceProductType,
)
from nautilus_trader.model import (
    AccountId,
    CryptoPerpetual,
    OmsType,
    Price,
    Quantity,
)

DEMO_ENVIRONMENT: Final = "BINANCE_DEMO_USD_M"
DEMO_INSTRUMENT_ID: Final = "BTCUSDT-PERP.BINANCE"
DEMO_SYMBOL: Final = "BTCUSDT"
DEMO_API_KEY_ENV: Final = "BINANCE_DEMO_API_KEY"
DEMO_API_SECRET_ENV: Final = "BINANCE_DEMO_API_SECRET"
OPERATOR_CONFIRMATION_TOKEN: Final = "ONE_WAY_ISOLATED_1X_FLAT_CONFIRMED"

NAUTILUS_DISTRIBUTION: Final = "nautilus-trader"
NAUTILUS_VERSION: Final = "2.0.0rc4"
NAUTILUS_UPSTREAM_COMMIT: Final = "a0400251110653b6d8ae6a9b5b89c4543fa85a2d"
NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256: Final = (
    "0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005"
)
_NAUTILUS_WHEEL_SUFFIX: Final = "cp313-cp313-manylinux_2_34_x86_64.whl"
_SHA256_PATTERN: Final = r"[0-9a-f]{64}"
_GIT_SHA_PATTERN: Final = r"[0-9a-f]{40}"


class Stage4DemoAdmissionError(ValueError):
    """Raised when the offline Demo boundary cannot be proven."""


class DeadlinePhase(StrEnum):
    CONNECT = "connect"
    READY = "ready"
    DATA_OBSERVATION = "data_observation"
    PRICE_READINESS = "price_readiness"
    ORDER_ACCEPTANCE = "order_acceptance"
    MARKET_OR_REDUCE_ONLY_FILL = "market_or_reduce_only_fill"
    CANCELLATION = "cancellation"
    RECONCILIATION = "reconciliation"
    CLEANUP = "cleanup"


DEADLINE_SECONDS: Final[tuple[tuple[DeadlinePhase, int], ...]] = (
    (DeadlinePhase.CONNECT, 30),
    (DeadlinePhase.READY, 60),
    (DeadlinePhase.DATA_OBSERVATION, 60),
    (DeadlinePhase.PRICE_READINESS, 10),
    (DeadlinePhase.ORDER_ACCEPTANCE, 10),
    (DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL, 30),
    (DeadlinePhase.CANCELLATION, 10),
    (DeadlinePhase.RECONCILIATION, 30),
    (DeadlinePhase.CLEANUP, 60),
)


@dataclass(frozen=True)
class DemoDeadline:
    """A fixed deadline beginning when its action enters the local queue."""

    phase: DeadlinePhase
    queued_at_ns: int
    expires_at_ns: int

    def expired(self) -> bool:
        return _monotonic_ns() >= self.expires_at_ns

    def remaining_seconds(self) -> float:
        remaining_ns = max(0, self.expires_at_ns - _monotonic_ns())
        return remaining_ns / 1_000_000_000


def start_queue_deadline(phase: DeadlinePhase) -> DemoDeadline:
    """Start one of the non-configurable Stage 4 deadlines."""
    duration_seconds = _deadline_seconds(phase)
    queued_at_ns = _monotonic_ns()
    return DemoDeadline(
        phase=phase,
        queued_at_ns=queued_at_ns,
        expires_at_ns=queued_at_ns + duration_seconds * 1_000_000_000,
    )


@dataclass(frozen=True)
class RuntimeIdentity:
    source_commit: str
    dependency_lock_sha256: str
    distribution: str
    version: str
    upstream_commit: str
    wheel_sha256: str
    python_implementation: str
    python_major: int
    python_minor: int
    operating_system: str
    machine: str


@dataclass(frozen=True)
class DemoConfig:
    """The deliberately singular, replaceable-for-validation Demo configuration."""

    environment: BinanceEnvironment = field(
        default_factory=lambda: BinanceEnvironment.DEMO
    )
    product_type: BinanceProductType = field(
        default_factory=lambda: BinanceProductType.USD_M
    )
    base_url_http: str | None = None
    base_url_ws: str | None = None
    base_url_ws_trading: str | None = None
    instrument_ids: tuple[str, ...] = (DEMO_INSTRUMENT_ID,)
    account_count: int = 1
    oms_type: OmsType = field(default_factory=lambda: OmsType.NETTING)
    one_way: bool = True
    margin_type: BinanceMarginType = field(
        default_factory=lambda: BinanceMarginType.ISOLATED
    )
    leverage: int = 1
    use_position_ids: bool = True
    max_active_or_inflight_orders: int = 1
    credential_environment_variables: tuple[str, ...] = (
        DEMO_API_KEY_ENV,
        DEMO_API_SECRET_ENV,
    )
    allow_entry_credential_override: bool = False


@dataclass(frozen=True)
class DemoCredentialSnapshot:
    """A batch-owned secret snapshot which is never serialized by this module."""

    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


@dataclass(frozen=True)
class FrozenDemoConfig:
    """Immutable canonical JSON plus its sole digest."""

    _payload_json: str = field(repr=False)
    config_digest: str

    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self._payload_json))

    def canonical_json(self) -> str:
        return self._payload_json


@dataclass(frozen=True)
class AdmittedDemoAttempt:
    """Proof that an order-enabled attempt passed every offline gate."""

    runtime: RuntimeIdentity
    config: DemoConfig
    frozen_config: FrozenDemoConfig
    _credentials: DemoCredentialSnapshot = field(repr=False)


def capture_runtime_identity(repository_root: Path) -> RuntimeIdentity:
    """Capture and validate the minimal current checkout/runtime identity."""
    root = Path(repository_root).resolve()
    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        raise Stage4DemoAdmissionError("dependency lock is missing")
    repository_top = _run_git(root, "rev-parse", "--show-toplevel")
    if Path(repository_top).resolve() != root:
        raise Stage4DemoAdmissionError("repository root identity does not match")
    source_commit = _run_git(root, "rev-parse", "HEAD")
    if re.fullmatch(_GIT_SHA_PATTERN, source_commit) is None:
        raise Stage4DemoAdmissionError("source commit identity is invalid")
    if _run_git(root, "status", "--porcelain", "--untracked-files=no"):
        raise Stage4DemoAdmissionError("tracked source is not clean")

    lock_bytes = lock_path.read_bytes()
    lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    wheel_sha256 = _locked_nautilus_wheel_sha256(lock_bytes)
    installed = distribution(NAUTILUS_DISTRIBUTION)
    installed_name = installed.metadata.get("Name")
    if installed_name != NAUTILUS_DISTRIBUTION or installed.version != NAUTILUS_VERSION:
        raise Stage4DemoAdmissionError("installed distribution identity does not match")

    identity = RuntimeIdentity(
        source_commit=source_commit,
        dependency_lock_sha256=lock_sha256,
        distribution=installed_name,
        version=installed.version,
        upstream_commit=NAUTILUS_UPSTREAM_COMMIT,
        wheel_sha256=wheel_sha256,
        python_implementation=platform.python_implementation(),
        python_major=sys.version_info.major,
        python_minor=sys.version_info.minor,
        operating_system=platform.system(),
        machine=platform.machine(),
    )
    _validate_runtime_constants(identity)
    return identity


def admit_current_demo_attempt(
    *,
    repository_root: Path,
    expected_runtime: RuntimeIdentity,
    config: DemoConfig,
    environ: Mapping[str, str],
    operator_token: str,
) -> AdmittedDemoAttempt:
    """Validate current facts before any caller is permitted to create a client."""
    observed_runtime = capture_runtime_identity(repository_root)
    return _admit_captured_demo_attempt(
        expected_runtime=expected_runtime,
        observed_runtime=observed_runtime,
        config=config,
        environ=environ,
        operator_token=operator_token,
    )


def _admit_captured_demo_attempt(
    *,
    expected_runtime: RuntimeIdentity,
    observed_runtime: RuntimeIdentity,
    config: DemoConfig,
    environ: Mapping[str, str],
    operator_token: str,
) -> AdmittedDemoAttempt:
    """Pure admission seam used after current facts have been captured."""
    _validate_runtime_constants(observed_runtime)
    if observed_runtime != expected_runtime:
        raise Stage4DemoAdmissionError("runtime identity drifted")
    _validate_demo_config(config)
    if operator_token != OPERATOR_CONFIRMATION_TOKEN:
        raise Stage4DemoAdmissionError("operator confirmation gate is invalid")
    credentials = _load_demo_credentials(environ)
    frozen_config = freeze_demo_config(config)
    return AdmittedDemoAttempt(
        runtime=observed_runtime,
        config=config,
        frozen_config=frozen_config,
        _credentials=credentials,
    )


def build_execution_client_config(
    admission: AdmittedDemoAttempt,
    *,
    account_id: AccountId,
) -> BinanceExecutionClientConfig:
    """Build the fixed rc4 config from the admitted batch credential snapshot."""
    config = admission.config
    return BinanceExecutionClientConfig(
        account_id=account_id,
        product_type=config.product_type,
        environment=config.environment,
        base_url_http=None,
        base_url_ws=None,
        base_url_ws_trading=None,
        use_position_ids=True,
        oms_type=OmsType.NETTING,
        api_key=admission._credentials.api_key,
        api_secret=admission._credentials.api_secret,
        futures_leverages={DEMO_SYMBOL: 1},
        futures_margin_types={DEMO_SYMBOL: BinanceMarginType.ISOLATED},
    )


def minimum_order_quantity(
    instrument: CryptoPerpetual,
    price: Price | None,
) -> Quantity:
    """Return the unique minimum valid normal-order quantity."""
    _require_demo_instrument(instrument)
    if price is None:
        raise Stage4DemoAdmissionError("quantity price is missing")
    price_value = price.as_decimal()
    if price_value <= 0:
        raise Stage4DemoAdmissionError("quantity price must be positive")
    step = instrument.size_increment.as_decimal()
    minimum = instrument.min_quantity
    if step <= 0 or minimum is None or minimum.as_decimal() <= 0:
        raise Stage4DemoAdmissionError("required public quantity constraint is missing")

    lower_bound = minimum.as_decimal()
    if instrument.min_notional is not None:
        lower_bound = max(
            lower_bound,
            instrument.min_notional.as_decimal() / price_value,
        )
    step_count = (lower_bound / step).to_integral_value(rounding=ROUND_CEILING)
    result = step_count * step
    maximum = instrument.max_quantity
    if maximum is not None and result > maximum.as_decimal():
        raise Stage4DemoAdmissionError(
            "minimum valid quantity exceeds maximum quantity"
        )
    return _exact_quantity(result, instrument, require_minimum=True)


def exact_reduce_only_quantity(
    instrument: CryptoPerpetual,
    net_position: Decimal,
) -> Quantity:
    """Return abs(position) without rounding, enlargement, or MIN_NOTIONAL."""
    _require_demo_instrument(instrument)
    result = abs(net_position)
    if result <= 0:
        raise Stage4DemoAdmissionError("cleanup position must be non-zero")
    return _exact_quantity(result, instrument, require_minimum=True)


def freeze_demo_config(config: DemoConfig) -> FrozenDemoConfig:
    """Return the closed, secret-free Stage 4 config payload and digest."""
    _validate_demo_config(config)
    payload: dict[str, object] = {
        "runtime": {
            "distribution": NAUTILUS_DISTRIBUTION,
            "version": NAUTILUS_VERSION,
            "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
            "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
            "python": "CPython 3.13",
            "platform": "linux-x86_64",
        },
        "environment": DEMO_ENVIRONMENT,
        "endpoint_policy": {"custom_endpoints_allowed": False},
        "instrument_allowlist": [DEMO_INSTRUMENT_ID],
        "account_mode": {
            "position": "ONE_WAY",
            "oms_type": "NETTING",
            "margin": "ISOLATED",
            "leverage": 1,
            "use_position_ids": True,
        },
        "order_cap": {"max_active_or_inflight": 1},
        "quantity_policy": {
            "normal": "minimum_valid_step_from_public_constraints",
            "reduce_only": "exact_absolute_position_without_rounding_or_min_notional",
        },
        "deadlines_seconds": {
            phase.value: seconds for phase, seconds in DEADLINE_SECONDS
        },
        "credential_environment_variables": [
            DEMO_API_KEY_ENV,
            DEMO_API_SECRET_ENV,
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return FrozenDemoConfig(
        _payload_json=encoded,
        config_digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def _validate_runtime_constants(identity: RuntimeIdentity) -> None:
    if re.fullmatch(_GIT_SHA_PATTERN, identity.source_commit) is None:
        raise Stage4DemoAdmissionError("source commit identity is invalid")
    if re.fullmatch(_SHA256_PATTERN, identity.dependency_lock_sha256) is None:
        raise Stage4DemoAdmissionError("dependency lock identity is invalid")
    expected = (
        identity.distribution == NAUTILUS_DISTRIBUTION
        and identity.version == NAUTILUS_VERSION
        and identity.upstream_commit == NAUTILUS_UPSTREAM_COMMIT
        and identity.wheel_sha256 == NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256
        and identity.python_implementation == "CPython"
        and (identity.python_major, identity.python_minor) == (3, 13)
        and identity.operating_system == "Linux"
        and identity.machine == "x86_64"
    )
    if not expected:
        raise Stage4DemoAdmissionError("runtime platform identity does not match")


def _validate_demo_config(config: DemoConfig) -> None:
    valid = (
        config.environment == BinanceEnvironment.DEMO
        and config.product_type == BinanceProductType.USD_M
        and config.base_url_http is None
        and config.base_url_ws is None
        and config.base_url_ws_trading is None
        and config.instrument_ids == (DEMO_INSTRUMENT_ID,)
        and config.account_count == 1
        and config.oms_type == OmsType.NETTING
        and config.one_way is True
        and config.margin_type == BinanceMarginType.ISOLATED
        and config.leverage == 1
        and config.use_position_ids is True
        and config.max_active_or_inflight_orders == 1
        and config.credential_environment_variables
        == (DEMO_API_KEY_ENV, DEMO_API_SECRET_ENV)
        and config.allow_entry_credential_override is False
    )
    if not valid:
        raise Stage4DemoAdmissionError(
            "Demo configuration is outside the supported boundary"
        )


def _load_demo_credentials(environ: Mapping[str, str]) -> DemoCredentialSnapshot:
    api_key = environ.get(DEMO_API_KEY_ENV)
    api_secret = environ.get(DEMO_API_SECRET_ENV)
    if api_key is None or not api_key.strip():
        raise Stage4DemoAdmissionError("Demo API key credential is missing")
    if api_secret is None or not api_secret.strip():
        raise Stage4DemoAdmissionError("Demo API secret credential is missing")
    return DemoCredentialSnapshot(api_key=api_key, api_secret=api_secret)


def _require_demo_instrument(instrument: CryptoPerpetual) -> None:
    if str(instrument.id) != DEMO_INSTRUMENT_ID:
        raise Stage4DemoAdmissionError("quantity instrument is outside the allowlist")


def _exact_quantity(
    value: Decimal,
    instrument: CryptoPerpetual,
    *,
    require_minimum: bool,
) -> Quantity:
    step = instrument.size_increment.as_decimal()
    minimum = instrument.min_quantity
    if step <= 0 or minimum is None:
        raise Stage4DemoAdmissionError("required public quantity constraint is missing")
    if value % step != 0:
        raise Stage4DemoAdmissionError(
            "quantity cannot be represented without rounding"
        )
    if require_minimum and value < minimum.as_decimal():
        raise Stage4DemoAdmissionError("quantity is below minimum quantity")
    maximum = instrument.max_quantity
    if maximum is not None and value > maximum.as_decimal():
        raise Stage4DemoAdmissionError("quantity exceeds maximum quantity")
    try:
        quantity = Quantity.from_decimal_dp(value, instrument.size_precision)
    except (OverflowError, ValueError) as exc:
        raise Stage4DemoAdmissionError(
            "quantity cannot be represented without rounding"
        ) from exc
    if quantity.as_decimal() != value:
        raise Stage4DemoAdmissionError(
            "quantity cannot be represented without rounding"
        )
    return quantity


def _locked_nautilus_wheel_sha256(lock_bytes: bytes) -> str:
    try:
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise Stage4DemoAdmissionError("dependency lock is invalid") from exc
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise Stage4DemoAdmissionError("dependency lock package list is invalid")
    matches: list[str] = []
    for package in packages:
        if (
            not isinstance(package, dict)
            or package.get("name") != NAUTILUS_DISTRIBUTION
        ):
            continue
        if package.get("version") != NAUTILUS_VERSION:
            raise Stage4DemoAdmissionError("locked Nautilus version does not match")
        wheels = package.get("wheels")
        if not isinstance(wheels, list):
            raise Stage4DemoAdmissionError("locked Nautilus wheels are missing")
        for wheel in wheels:
            if not isinstance(wheel, dict):
                continue
            url = wheel.get("url")
            wheel_hash = wheel.get("hash")
            if isinstance(url, str) and url.endswith(_NAUTILUS_WHEEL_SUFFIX):
                if isinstance(wheel_hash, str) and wheel_hash.startswith("sha256:"):
                    matches.append(wheel_hash.removeprefix("sha256:"))
    if matches != [NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256]:
        raise Stage4DemoAdmissionError("locked Nautilus wheel identity does not match")
    return matches[0]


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Stage4DemoAdmissionError(
            "source repository identity is unavailable"
        ) from exc
    return completed.stdout.strip()


def _monotonic_ns() -> int:
    return time.monotonic_ns()


def _deadline_seconds(phase: DeadlinePhase) -> int:
    for configured_phase, seconds in DEADLINE_SECONDS:
        if phase == configured_phase:
            return seconds
    raise Stage4DemoAdmissionError("deadline phase is outside the fixed policy")

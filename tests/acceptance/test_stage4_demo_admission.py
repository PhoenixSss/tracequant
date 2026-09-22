from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.adapters.binance import (
    BinanceEnvironment,
    BinanceMarginType,
    BinanceProductType,
)
from nautilus_trader.model import (
    AccountId,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    OmsType,
    Price,
    Quantity,
    Symbol,
)

from tracequant.integrations.nautilus import stage4_demo
from tracequant.integrations.nautilus.stage4_demo import (
    DEADLINE_SECONDS,
    DEMO_API_KEY_ENV,
    DEMO_API_SECRET_ENV,
    DEMO_INSTRUMENT_ID,
    OPERATOR_CONFIRMATION_TOKEN,
    AdmittedDemoAttempt,
    DeadlinePhase,
    DemoConfig,
    RuntimeIdentity,
    Stage4DemoAdmissionError,
    admit_current_demo_attempt,
    build_execution_client_config,
    capture_runtime_identity,
    exact_reduce_only_quantity,
    freeze_demo_config,
    minimum_order_quantity,
    prepare_demo_admission_batch,
    start_queue_deadline,
)

VALID_ENVIRONMENT = {
    DEMO_API_KEY_ENV: "demo-key-value",
    DEMO_API_SECRET_ENV: "demo-secret-value",
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime_checkout(tmp_path: Path) -> tuple[Path, RuntimeIdentity]:
    (tmp_path / "uv.lock").write_bytes((REPOSITORY_ROOT / "uv.lock").read_bytes())
    commands = (
        ("git", "init", "--quiet"),
        ("git", "add", "uv.lock"),
        (
            "git",
            "-c",
            "user.name=TraceQuant Test",
            "-c",
            "user.email=tracequant@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "runtime fixture",
        ),
    )
    for command in commands:
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path, capture_runtime_identity(tmp_path)


def _instrument(
    *,
    minimum: str = "0.001",
    maximum: str | None = "10.000",
    minimum_notional: str | None = "5.00",
    size_increment: str = "0.001",
    size_precision: int = 3,
) -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(DEMO_INSTRUMENT_ID),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=2,
        size_precision=size_precision,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str(size_increment),
        min_quantity=Quantity.from_str(minimum),
        max_quantity=Quantity.from_str(maximum) if maximum is not None else None,
        min_notional=(
            Money.from_str(f"{minimum_notional} USDT")
            if minimum_notional is not None
            else None
        ),
        ts_event=0,
        ts_init=0,
    )


def _admit(
    runtime_checkout: tuple[Path, RuntimeIdentity],
    *,
    config: DemoConfig = DemoConfig(),
    expected_runtime: RuntimeIdentity | None = None,
    environ: dict[str, str] | None = None,
    operator_token: str = OPERATOR_CONFIRMATION_TOKEN,
) -> None:
    repository_root, runtime = runtime_checkout
    batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=config,
        expected_runtime=expected_runtime or runtime,
        environ=VALID_ENVIRONMENT if environ is None else environ,
    )
    admit_current_demo_attempt(
        batch=batch,
        operator_token=operator_token,
    )


def test_stage4_demo_admission_rejects_each_enumerated_invalid_configuration(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    invalid_configs = [
        replace(DemoConfig(), environment=BinanceEnvironment.LIVE),
        replace(DemoConfig(), environment=BinanceEnvironment.TESTNET),
        replace(DemoConfig(), product_type=BinanceProductType.SPOT),
        replace(DemoConfig(), product_type=BinanceProductType.COIN_M),
        replace(DemoConfig(), base_url_http="https://example.invalid"),
        replace(DemoConfig(), base_url_ws="wss://example.invalid"),
        replace(DemoConfig(), base_url_ws_trading="wss://example.invalid"),
        replace(
            DemoConfig(), instrument_ids=(DEMO_INSTRUMENT_ID, "ETHUSDT-PERP.BINANCE")
        ),
        replace(DemoConfig(), account_count=2),
        replace(DemoConfig(), oms_type=OmsType.HEDGING),
        replace(DemoConfig(), one_way=False),
        replace(DemoConfig(), margin_type=BinanceMarginType.CROSS),
        replace(DemoConfig(), leverage=2),
        replace(DemoConfig(), use_position_ids=False),
        replace(DemoConfig(), max_active_or_inflight_orders=2),
        replace(
            DemoConfig(),
            credential_environment_variables=("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        ),
        replace(DemoConfig(), allow_entry_credential_override=True),
    ]
    for invalid_config in invalid_configs:
        with pytest.raises(Stage4DemoAdmissionError):
            _admit(runtime_checkout, config=invalid_config)

    runtime = runtime_checkout[1]
    runtime_drifts = [
        replace(runtime, source_commit="3" * 40),
        replace(runtime, dependency_lock_sha256="4" * 64),
    ]
    for drifted_runtime in runtime_drifts:
        with pytest.raises(Stage4DemoAdmissionError, match="drifted"):
            _admit(runtime_checkout, expected_runtime=drifted_runtime)

    invalid_runtime_constants = [
        replace(runtime, distribution="other"),
        replace(runtime, version="2.0.0rc5"),
        replace(runtime, upstream_commit="5" * 40),
        replace(runtime, wheel_sha256="6" * 64),
        replace(runtime, python_implementation="PyPy"),
        replace(runtime, python_minor=14),
        replace(runtime, operating_system="Darwin"),
        replace(runtime, machine="aarch64"),
    ]
    for invalid_runtime in invalid_runtime_constants:
        with pytest.raises(Stage4DemoAdmissionError):
            _admit(runtime_checkout, expected_runtime=invalid_runtime)

    for credentials in (
        {},
        {DEMO_API_KEY_ENV: "", DEMO_API_SECRET_ENV: "secret"},
        {DEMO_API_KEY_ENV: "key", DEMO_API_SECRET_ENV: ""},
        {"BINANCE_API_KEY": "live-key", "BINANCE_API_SECRET": "live-secret"},
    ):
        with pytest.raises(Stage4DemoAdmissionError):
            _admit(runtime_checkout, environ=credentials)

    for token in ("", "confirmed", "ONE_WAY_ISOLATED_1X_CONFIRMED"):
        with pytest.raises(Stage4DemoAdmissionError):
            _admit(runtime_checkout, operator_token=token)


def test_valid_admission_is_offline_stable_and_secret_free(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    repository_root, runtime = runtime_checkout
    batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=DemoConfig(),
        expected_runtime=runtime,
        environ={
            **VALID_ENVIRONMENT,
            "BINANCE_API_KEY": "ignored-live-key",
            "BINANCE_API_SECRET": "ignored-live-secret",
        },
    )
    admission = admit_current_demo_attempt(
        batch=batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )
    frozen = admission.frozen_config
    payload = frozen.payload()
    assert payload == freeze_demo_config(DemoConfig()).payload()
    assert (
        frozen.config_digest
        == hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    rendered = frozen.canonical_json() + repr(admission)
    assert "demo-key-value" not in rendered
    assert "demo-secret-value" not in rendered
    assert "ignored-live-key" not in rendered
    assert OPERATOR_CONFIRMATION_TOKEN not in rendered
    assert set(payload) == {
        "runtime",
        "environment",
        "endpoint_policy",
        "instrument_allowlist",
        "account_mode",
        "order_cap",
        "quantity_policy",
        "deadlines_seconds",
        "credential_environment_variables",
    }
    assert "source_commit" not in frozen.canonical_json()
    assert "dependency_lock_sha256" not in frozen.canonical_json()

    client_config = build_execution_client_config(
        admission,
        account_id=AccountId.from_str("BINANCE-001"),
    )
    assert client_config.environment == BinanceEnvironment.DEMO
    assert client_config.product_type == BinanceProductType.USD_M
    assert client_config.oms_type == OmsType.NETTING
    assert client_config.use_position_ids is True
    assert client_config.base_url_http is None
    assert client_config.base_url_ws is None
    assert client_config.base_url_ws_trading is None
    assert client_config.instrument_provider.load_all is False
    assert client_config.instrument_provider.load_ids == [DEMO_INSTRUMENT_ID]
    assert client_config.futures_leverages == {"BTCUSDT": 1}
    assert client_config.futures_margin_types == {"BTCUSDT": BinanceMarginType.ISOLATED}

    assert (
        stage4_demo._locked_nautilus_wheel_sha256(
            (REPOSITORY_ROOT / "uv.lock").read_bytes()
        )
        == stage4_demo.NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256
    )


def test_execution_config_rejects_forged_or_replaced_admission(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    repository_root, runtime = runtime_checkout
    batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=DemoConfig(),
        expected_runtime=runtime,
        environ=VALID_ENVIRONMENT,
    )
    admission = admit_current_demo_attempt(
        batch=batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )
    alternate_credentials = stage4_demo.DemoCredentialSnapshot(
        api_key="live-key",
        api_secret="live-secret",
    )

    with pytest.raises(Stage4DemoAdmissionError, match="only be created"):
        AdmittedDemoAttempt(
            runtime=runtime,
            config=replace(DemoConfig(), environment=BinanceEnvironment.LIVE),
            frozen_config=admission.frozen_config,
            _credentials=alternate_credentials,
        )
    with pytest.raises(Stage4DemoAdmissionError, match="only be created"):
        replace(
            admission,
            config=replace(DemoConfig(), environment=BinanceEnvironment.LIVE),
        )
    with pytest.raises(Stage4DemoAdmissionError, match="only be created"):
        replace(admission, _credentials=alternate_credentials)

    missing_seal = object.__new__(AdmittedDemoAttempt)
    object.__setattr__(missing_seal, "runtime", runtime)
    object.__setattr__(missing_seal, "config", DemoConfig())
    object.__setattr__(missing_seal, "frozen_config", admission.frozen_config)
    object.__setattr__(missing_seal, "_credentials", alternate_credentials)
    with pytest.raises(Stage4DemoAdmissionError, match="proof is invalid"):
        build_execution_client_config(
            missing_seal,
            account_id=AccountId.from_str("BINANCE-001"),
        )


def test_batch_snapshot_prevents_cross_attempt_credential_drift(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    repository_root, runtime = runtime_checkout
    environ = dict(VALID_ENVIRONMENT)
    batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=DemoConfig(),
        expected_runtime=runtime,
        environ=environ,
    )
    first = admit_current_demo_attempt(
        batch=batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )

    environ[DEMO_API_KEY_ENV] = "drifted-key"
    environ[DEMO_API_SECRET_ENV] = "drifted-secret"
    second = admit_current_demo_attempt(
        batch=batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )

    assert first._credentials is second._credentials
    assert first._credentials.api_key == VALID_ENVIRONMENT[DEMO_API_KEY_ENV]
    assert first._credentials.api_secret == VALID_ENVIRONMENT[DEMO_API_SECRET_ENV]


def test_secret_bearing_objects_have_no_credential_derived_hash(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    repository_root, runtime = runtime_checkout
    first_batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=DemoConfig(),
        expected_runtime=runtime,
        environ=VALID_ENVIRONMENT,
    )
    second_batch = prepare_demo_admission_batch(
        repository_root=repository_root,
        config=DemoConfig(),
        expected_runtime=runtime,
        environ={
            DEMO_API_KEY_ENV: "other-key",
            DEMO_API_SECRET_ENV: "other-secret",
        },
    )
    first = admit_current_demo_attempt(
        batch=first_batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )
    second = admit_current_demo_attempt(
        batch=second_batch,
        operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )

    assert first._credentials is not second._credentials
    assert type(first._credentials).__hash__ is object.__hash__
    assert first_batch == second_batch
    assert first == second


def test_runtime_capture_rejects_tracked_source_changes(
    runtime_checkout: tuple[Path, RuntimeIdentity],
) -> None:
    repository_root, _ = runtime_checkout
    (repository_root / "uv.lock").write_text("tracked drift", encoding="utf-8")
    with pytest.raises(Stage4DemoAdmissionError, match="not clean"):
        capture_runtime_identity(repository_root)


def test_normal_quantity_is_the_unique_minimum_public_step() -> None:
    instrument = _instrument()
    quantity = minimum_order_quantity(instrument, Price.from_str("3000.00"))
    assert quantity.as_decimal() == Decimal("0.002")
    assert quantity.precision == instrument.size_precision

    no_notional = _instrument(minimum="0.003", minimum_notional=None)
    assert minimum_order_quantity(
        no_notional, Price.from_str("3000.00")
    ).as_decimal() == Decimal("0.003")

    with pytest.raises(Stage4DemoAdmissionError, match="positive"):
        minimum_order_quantity(instrument, Price.from_str("0.00"))
    with pytest.raises(Stage4DemoAdmissionError, match="missing"):
        minimum_order_quantity(instrument, None)
    with pytest.raises(Stage4DemoAdmissionError, match="exceeds maximum"):
        minimum_order_quantity(_instrument(maximum="0.001"), Price.from_str("3000.00"))


def test_exact_cleanup_never_rounds_enlarges_or_applies_min_notional() -> None:
    instrument = _instrument(minimum_notional="100000.00")
    assert exact_reduce_only_quantity(
        instrument, Decimal("-0.003")
    ).as_decimal() == Decimal("0.003")
    with pytest.raises(Stage4DemoAdmissionError, match="without rounding"):
        exact_reduce_only_quantity(instrument, Decimal("0.0035"))
    with pytest.raises(Stage4DemoAdmissionError, match="non-zero"):
        exact_reduce_only_quantity(instrument, Decimal("0.000"))
    with pytest.raises(Stage4DemoAdmissionError, match="below minimum"):
        exact_reduce_only_quantity(_instrument(minimum="0.002"), Decimal("0.001"))
    with pytest.raises(Stage4DemoAdmissionError, match="exceeds maximum"):
        exact_reduce_only_quantity(instrument, Decimal("10.001"))


def test_deadlines_are_fixed_and_start_at_queue_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert {phase.value: value for phase, value in DEADLINE_SECONDS} == {
        "connect": 30,
        "ready": 60,
        "data_observation": 60,
        "price_readiness": 10,
        "order_acceptance": 10,
        "market_or_reduce_only_fill": 30,
        "cancellation": 10,
        "reconciliation": 30,
        "cleanup": 60,
    }
    readings = iter((10_000_000_000, 39_999_999_999, 40_000_000_000))
    monkeypatch.setattr(stage4_demo, "_monotonic_ns", lambda: next(readings))
    deadline = start_queue_deadline(DeadlinePhase.CONNECT)
    assert deadline.queued_at_ns == 10_000_000_000
    assert deadline.remaining_seconds() > 0
    assert deadline.expired()

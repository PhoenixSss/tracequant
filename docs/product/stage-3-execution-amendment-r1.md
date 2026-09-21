# Stage 3 execution amendment r1

This amendment is authorized for Task #365 and applies to both Stage 3
strategies and their accounting-only replays. It supplements the frozen
[v0.1 requirements](stage-3-strategy-and-model-requirements.md), superseding
only the execution and funding comparison rules identified below.
Its path and SHA-256 are bound into run identities and the OOS rebuild contract.

## B1 execution price (§3.1)

The next-bar open execution tick uses the accepted bar's open, converted by
the locked Nautilus runtime's `instrument.make_price` using the frozen
instrument's price increment and precision. For example, `46796.15` with a
`0.10` increment becomes `46796.20`. Both strategies and accounting replays
use this same tick. The execution timestamp remains strictly after the
decision timestamp. Catalog bars, features and labels retain their original
prices; this conversion changes the execution price only.

## Minimum order boundary (§3.2)

An executable quantity must meet the larger of the frozen size increment
and minimum quantity. When the instrument specifies a minimum notional,
`quantity × price >= min_notional` must also hold; equality is accepted.
An absent minimum notional introduces no additional floor.

The decision-time delta is checked at the closed-bar price, and the actual
executable delta is checked again at the B1 execution price. A delta below
either floor is recorded as a no-op. For a reversal, the B1 check applies to
the close leg's quantity, not the combined opposite-side delta. After a flat
position is confirmed, a reversal open below either floor fails closed before
submission. The existing close-then-open confirmation sequence remains in force.
These checks apply equally to both strategies through their shared execution
implementation and do not resize the frozen `10000` USDT target.

## Native funding rounding (§3.7 F-funding)

The zero-funding fixture must still produce exactly zero, and the base fixture
must still have a nonzero native funding result. Compare the native double-rate
result with twice the native base result using:

```text
abs(double_funding - 2 * base_funding) <= N * q
q = 10 ** (-USDT.precision)
N = sum(base funding events' native_account_event_count)
```

The locked runtime currently has `q = 0.00000001 USDT`. Each count must be a
nonnegative integer, and `N` must be positive. This is a bound of one settlement
quantum per observed native account event, accounting for independent native
rounding of base and double-rate settlements. It is not a relative tolerance
and must not be adjusted using observed performance. Exceeding the bound fails
the fixture. Record the actual native double result, `2 * base_funding`, and
the bound in external evaluation evidence. Fee scaling retains its existing
checks. All accounting results still come from Nautilus; no second ledger is
introduced.

## Evidence and scope

These changes can alter decisions, fills, positions and metrics. Previous
acceptance evidence must be regenerated from the final clean implementation,
twice into distinct new external partitions, and stable digests must agree.
The rebuild freezes clean Git identity, the full lock checksum and the rebuild
contract digest before evaluation and rechecks them immediately before
publishing the tracked acceptance record. Any drift leaves that record unchanged.

No Stage 2 data, feature/label definition, training parameters, signal rules,
folds, base fee rates, starting balance or cost threshold are changed by this
amendment. Results remain `OFFLINE_BACKTEST_ONLY` and `LIVE_NOT_APPROVED`;
software acceptance grants no Demo or Live admission.

# Compact Success Path Example

## Delivery

1. `delivery` snapshot establishes Task/PR/repository identity.
2. Named `targeted` profiles support development.
3. `workflow-delivery --base-sha <BASE>` performs the phase validation and required Skill validators in one fixed call.
4. `delivery-readiness` provides the final stable snapshot.
5. The Skill reports the compact digests and does not replay the internal GitHub queries or validation logs.

## Review

1. Lock base/head in a fresh read-only session.
2. Extract trusted-base Evidence/Validation front doors.
3. Run trusted `review`, inspect changed code and risks, then trusted `workflow-review`.
4. Run trusted `recheck` against the exact initial snapshot.
5. Emit one fixed verdict. No GitHub write, fix, merge, or Closeout occurs.

## Closeout

After the maintainer has merged, use `closeout-readonly`, synchronize `main` through the existing approved process, run `workflow-closeout`, and recheck. Only the exact verified Task branch may be cleaned under the existing approval contract.

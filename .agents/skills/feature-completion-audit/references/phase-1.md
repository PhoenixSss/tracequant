## Phase 1: identify and lock

Generate the Feature snapshot. Verify repository, open `type:feature`, canonical
title, complete body/comments/fields, Parent, blockers, dependencies,
Relationships, actual `origin/main`, and the direct-child collection.

Lock and report:

```text
Audited branch: origin/main
Audited main SHA
Feature canonical identity
Feature content / Relationship digest
Direct-child set and digest
Snapshot ID
```

Distinguish direct children from indirect descendants and related Issues. Use
the evidence-insufficient path when identity, child-set, or main facts are
unavailable or contradictory.


Generate the snapshot using:

```bash
uv run --frozen python -m tools.lck.feature_audit feature-audit-snapshot \
  --feature <FEATURE> --expected-main-sha <SHA>
```

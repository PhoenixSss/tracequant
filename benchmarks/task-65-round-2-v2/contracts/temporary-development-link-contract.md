# Temporary Development Link Contract — task-65-round-2-v2

Authoritative source: Issue #125 "Temporary Development Link Contract"
section. Governs the temporary branch↔#65 Development linkage used to bind
each Arm's business branch to Issue #65 without relying on closing keywords.

PR state: **OPEN, NON-DRAFT, body contains `Closes #65` (native contract),
DO NOT MERGE**.

## ENSURE-LINKED-BUSINESS-BRANCH CONTRACT (per Arm, serial, single-result)

Before Delivery the conductor must **ensure** (not create-then-relink) that:

- the exact deterministic business branch exists;
- branch tip == `ARM_CONTROL_BASE_SHA`;
- the branch carries no Task #65 implementation yet;
- the branch is Development-linked to #65;
- #65 is OPEN;
- no other v2 Arm currently holds an active Development linkage.

## Two mutually exclusive implementation paths

Exactly one path may be chosen per Arm setup:

1. **Preferred — native linked-branch mechanism**:
   `gh issue develop 65 --base <ARM_CONTROL_BASE_BRANCH> --name <ARM_BUSINESS_BRANCH>`
   (creates branch + Development link). If a capability probe proves this
   satisfies the contract, the same branch must **not** be manual-linked again
   afterwards.
2. **Fallback — plain git creation + one manual link**:
   `git create/push exact business branch` + one manual
   Development-link branch → #65.

The chosen path and the capability-probe result are recorded in the Arm's
protocol operations. Any same-Arm double-link attempt is a violation.

## Verification instead of re-linking

Whichever path is used, the final mechanical verification is the same:

- exact branch name;
- branch base/tip identity (tip == `ARM_CONTROL_BASE_SHA`);
- active Development linkage == current Arm only;
- #65 OPEN.

Any failure → **HUMAN GATE, the Arm must not start**.

## Subsequent flow (unchanged)

```text
Delivery
→ native Agent reuse exact branch (identity verification / recovery-reuse decision)
→ implement / commit / push
→ native OPEN NON-DRAFT PR create/reuse (on the exact pre-linked business branch)
→ verify branch→PR linkage promotion (GitHub promotes branch linkage to the
  current PR's Development linkage)
→ closingIssuesReferences exactly [65] (count == 1, items == [65], truncated == false)
→ native closing_linkage evidence gate PASS
→ #65 still OPEN; no other v2 Arm active Development linkage
→ Independent Review
→ final evidence/audit archive
→ conductor manually unlinks the current PR from #65
→ verify: #65 OPEN; no residual ACTIVE v2 Development linkage
→ next Arm
```

## Rules

- The conductor must **not** pre-create experimental PRs before Delivery; PR
  create/reuse is the measured generation's native Delivery behavior.
- **At most one v2 Arm** holds an active Development linkage with #65 at any
  time (branch-level or PR-level).
- link/unlink are **conductor actions** (setup/teardown), not part of the
  measured Agent's Delivery/Review, but they must be recorded in the Arm's
  evidence as manual intervention / protocol operations (operation list +
  timestamps + operation type + implementation path + verification result).
- **Agents must never create or delete Development linkage themselves.**
- **No bypassing**: no modifying `workflow_evidence.py`, no overriding the
  `closing_linkage` gate, no benchmark-specific fake evidence, no reliance on
  the closing keyword to produce `closingIssuesReferences`. After GitHub
  promotes branch linkage to PR linkage, `closingIssuesReferences` is the real
  data source — not an override, not fake evidence; the gate itself and its
  PASS definition (exact match `[task_number]`) keep the generation's native
  form.
- **PR created and contract not satisfied → HUMAN GATE**: terminate the Arm
  immediately, do **not** enter Independent Review, the current data must
  **not** enter the formal comparison; a repair requires a fresh workspace +
  fresh Delivery + fresh Review rerun.
- **#65 stays OPEN throughout**: verified before branch link, before Delivery
  start, after PR creation, after Review, after unlink. Any abnormal close →
  **BENCHMARK INVALID → HUMAN GATE**. DO NOT MERGE / no auto-merge / no
  closeout all remain in force.

## Timeline residue clarification

Unlink only clears the current **active** Development linkage; GitHub history
ConnectedEvent / DisconnectedEvent or other timeline metadata is **not**
assumed deleted. "No residual v2 experimental linkage" precisely means **no
residual ACTIVE v2 Development linkage** — historical timeline audit events
may exist but are subject to the access-audit forbidden-read rules. Any
previous-Arm PR number/URL, branch name, unique commit SHA, or other
current-run dynamic identity exposed in these timeline events is
**OTHER_ARM_CURRENT_RUN_SECRET** (Class 3); an Arm that actually reads those
timeline events → **BENCHMARK INFORMATION LEAKAGE** → current Arm INVALID →
fresh rerun.

Normal reading of #65 (title, body / frozen Task specification, labels, native
dependency metadata) is **not** leakage; only actually reading previous-Arm
dynamic identity triggers invalidation.

## Measurement boundary

ENSURE-LINKED-BUSINESS-BRANCH (branch creation + link, either path) is
**conductor setup** — it is not counted in the measured Agent Delivery
Token/workflow cost, but is recorded as a protocol operation. All four Arms
measure the generation-native **exact Task branch reuse/recovery path** (not a
fresh-branch-creation path) — this is the protocol-normalized setup for all
four Arms. The Agent's identity verification, recovery/reuse decision,
implementation, commit, push, native PR create/reuse, readiness, and handoff
on the existing exact branch remain part of the formal measurement; **PR
create/reuse must continue to be executed by the measured Agent**.

## Phase 6: gaps and stability

Classify each completion gap by severity and, when useful, propose the smallest
candidate Task boundary without creating or editing a Task.

Run the stability recheck:

```bash
uv run --frozen python -m tools.lck.feature_audit feature-audit-recheck \
  --snapshot-id <SNAPSHOT_ID>
```
 Recollect Feature identity/content,
Relationships, direct-child set, child lifecycle evidence, audited main, and
checks. Any audited-main, material Feature, Relationship, or direct-child-set
change invalidates the stable conclusion and requires a new independent audit.

## Findings and verdicts

Use exactly Blocking, High, Medium, Low, and Nit. Cite exact Feature clauses,
child Issues, current-main files, validation, or GitHub state. Any unresolved
Blocking/High/Medium finding prevents a passing verdict.

Output exactly one:

```text
Feature 已完成，可以由维护者人工收尾
```

Only when all necessary direct work is complete, every criterion is
`Satisfied` or approved `Not applicable`, current-main integration/tests/docs
are complete, validation/checks pass, no blocker or Blocking/High/Medium finding
remains, and the audited main, Feature, Relationships, and direct-child set are
stable.

```text
Feature 尚未完成，需要补充或修复 Task
```

When a confirmed Blocking/High/Medium gap remains, necessary child work is
incomplete, a criterion is not satisfied, current main lacks required
implementation/integration/tests/docs, validation fails, or a blocker remains.

```text
证据不足，暂不能判定 Feature 完成
```

When no confirmed defect can be concluded but criteria, children, GitHub facts,
current-main evidence, stability, or a maintainer decision are insufficient or
contradictory.

## Report and closeout gate

On a clean path, report Feature identity/URL/Parent, audited main SHA, actual
Skill/Runner identity, direct-child summary, acceptance matrix, integration and
safety summary, findings, validation/checks, blockers and state conflicts,
gap-to-Task recommendations, limitations, actions not performed, one fixed
verdict, and:

```text
Audited main SHA: <actual SHA>
```

Use a detailed report for gaps, failed/pending evidence, drift, fallback,
conflict, or maintainer decision.

After a passing audit, the maintainer must re-verify current `origin/main`,
Feature title/body, direct-child set, blockers, and checks immediately before
manual closeout. This Skill performs none of those writes and never assesses
Epic completion.

Remove any temporary worktree by exact path without destructive broad cleanup.
Re-run in a new independent session after any new merged Task, clarified
Feature, resolved blocker, repaired validation, main change, child-set change,
or reopened Feature. Never inherit an earlier verdict.

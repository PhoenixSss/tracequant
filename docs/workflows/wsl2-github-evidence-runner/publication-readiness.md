# Task #84 Publication Readiness

## Current status

| Material group | Status | Notes |
| --- | --- | --- |
| Fixed runner, profiles, schema, partial/failure contract | complete | Implemented and test-backed. |
| Git/GitHub approval boundary | complete | Write operations remain outside the allow boundary. |
| Security, credentials, rollback, troubleshooting | complete | No token or complete credential is committed. |
| Historical Git/`gh`, Guardian, and Token context | complete | Aggregate Task #63/#64 report metrics are committed without raw rollout logs. |
| Environment capability baseline | complete-with-live-refresh | Task #84 PR, Project, Required Checks, scopes, and tool versions refreshed. |
| Live fixed-profile evidence | complete-partial | Real review profile observed `partial` because Required Checks configuration is plan-limited 403; `quality` check passed. |
| Live snapshot recheck evidence | complete-partial-stable | Same-snapshot recheck was stable with no changed fields and the same Required Checks unknown gate. |
| Live execpolicy matrix | complete | Fixed profile prefixes allow; direct gh/Git/Python/shell are prompt, forbidden, or unmatched; runner rejects arbitrary tail. |
| Task #84 lifecycle identity | delivery-review | PR #102 exists at Delivery HEAD `3814af3d95ece48ef03c59536dd025f5fb5511fb`; Review/merge/closeout remain pending. |
| Task #65 candidate Token comparison | deferred | Task #86 owns the candidate experiment. |

## Final-document coverage

### 代理开发工作流设计指导手册

Ready material:

- fixed read-only entry and stage profiles;
- normalized evidence schema and exit semantics;
- snapshot/recheck and drift handling;
- partial/unknown and plan-limit behavior;
- Git/GitHub operation boundary;
- credential and environment capability guidance;
- Rules prefix boundary and complete runner argv validation;
- rollback and failure cases.

Live local evidence now adds a concrete end-to-end example and measured
operational values. It does not change the architecture.

### 代理工作流 Token 优化技术分享文章

Ready material:

- historical Task #63/#64 Git/`gh`, Guardian, and Token aggregates;
- one-fixed-invocation mechanism;
- internal API-operation accounting design;
- compact stdout and external evidence separation;
- explicit unsupported causal claims.

Still pending beyond this Task:

- Task #85 real Skill-path consolidation;
- Task #86 controlled candidate Token results.

## Merge-readiness material gate

Before independent Review, the Task #84 Delivery should have committed:

```text
live-profile-evidence.json
live-recheck-evidence.json
rules-live-evidence.json
environment-capability.json (refreshed)
external-live-evidence-manifest.json
```

Each file must distinguish `observed`, `derived`, `expected`,
`not-measured`, and `unavailable`. Missing live values must never be filled
from architecture expectations.

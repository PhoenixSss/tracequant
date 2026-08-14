# Evidence Runner live verification plan

Use this plan after the current Runner, profiles, Rules, and consuming Skill are
committed and the real Task/PR exists. Raw results and rollout logs remain in
ignored or external storage.

## Profile observation

From a fresh session, invoke the applicable standard Skill Phase. Capture from
the original rollout and Runner artifact:

- Task, PR, base SHA, head SHA, profile, and current Skill/Runner hashes;
- status, partial flag, exit code, snapshot ID/fingerprint;
- fixed GitHub/Git operation counts, duration, compact stdout bytes;
- result path and SHA-256;
- Guardian turns, approvals/elevation, retries, and model calls;
- Runner, `gh`, Git, uv, Codex, and environment versions when exposed.

Do not add Runner instructions or metric requests to the tested Skill prompt.

## Stability observation

Run the standard Phase that invokes `recheck`, or invoke the documented fixed
`recheck` entry when the workflow explicitly requires it. Record stable/changed
state, changed fields, status, operation counts, duration, and artifact identity.

## Rules observation

Use `codex execpolicy check` and a fresh live session to distinguish policy
routing from Runner semantic acceptance. Verify:

- fixed profiles route as intended;
- injected/trailing argv is rejected by the Runner;
- direct interpreters, shell wrappers, arbitrary GitHub/API calls, and Git writes
  are not broadly allowed;
- `gh auth token` is forbidden.

## Claim boundary

Classify results as `observed`, `derived`, `expected`, `not-measured`, or
`unavailable`. A successful live invocation proves the happy path and evidence
contract. It does not by itself prove workflow-wide Token reduction; that
comparison uses external Codex rollout analysis.

# Task #85 Live Migration Evidence Capture

Static source comparison proves that the Task Skills and policies were rewritten, but it does not prove runtime Guardian or Token savings. After the trusted files are committed and the Task #85 PR exists, Delivery records one bounded live observation of the new normal path.

## Capture set

Record:

- Task, PR, base, and final Delivery head;
- fixed Delivery preflight/readiness profile result IDs and SHA-256 values;
- fixed `workflow-delivery` profile result and command count;
- `skill_path_audit.py` digest and zero legacy-path result;
- real execpolicy decisions for fixed and dangerous paths;
- model-visible fixed Runner invocation count for the observed finalization sequence;
- Guardian turns, approval prompts, elevated executions, duration, and compact stdout bytes when exposed;
- expected versus observed differences;
- raw evidence external identities without committing `.agents/evidence.local/`, `.agents/validation.local/`, or rollout JSONL.

## Success claim boundary

A Task #85 live observation may prove that the migrated Skill path is executable, that fixed profiles return bounded evidence, and that legacy paths were not used in that observed sequence. It does not establish Task #65 Token reduction or workflow-wide causal improvement.

## Failure example

Intentionally use test doubles or existing tests to demonstrate one `partial`/`unknown` Evidence result and one Validation failure. Record that the Skill expands only the named gate/failed command and does not run the complete legacy chain. Do not manipulate the live PR or checks to manufacture failure.

## Final-head self-reference

Committed summaries may describe a material-capture head. The final-head readiness identity belongs in PR/readiness lifecycle evidence when committing it would create a new head. State the relationship explicitly.

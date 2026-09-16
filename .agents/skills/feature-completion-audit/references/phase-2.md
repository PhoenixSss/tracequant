## Phase 2: direct-child inventory

For every direct child, record:

- Issue number, title, type, state, labels, Parent, Project Status, blockers, and
  Relationships;
- whether it is necessary to Feature completion;
- merged/closing PR, merge commit, checks, and correct linkage;
- Task closeout/lifecycle facts when applicable;
- approved no-PR/no-code exception and its current-main evidence;
- current-main implementation, tests, docs, ADR, or approved-decision evidence.

Do not infer completion from child closure counts. A closed child without merged
or current-main evidence is not automatically satisfied. Report reopened,
orphaned, blocked, or ambiguously parented work.

# LCK context retrieval policy

The current leaf Issue body is the default full-text business input. LCK may
also acquire applicable repository instructions, relevant code and tests, the
minimum Skill input, and current Git/GitHub object identities.

Do not eagerly load complete Issue comments, Parent Feature or Epic bodies,
sibling Issues, all linked documents, all ADRs, roadmap material, or historical
workflow sessions. A link alone is not an instruction to read its full target.

Expand context only when the leaf explicitly references another requirement,
scope remains ambiguous, active sources conflict, a hard dependency affects
eligibility, safety or architecture is at risk, or Acceptance Criteria name a
fixture, protocol, report, or frozen artifact.

Expansion is progressive: read the minimum relevant section, reassess, and
expand only if still insufficient. Unbounded recursive expansion through
parents, comments, and links is forbidden. Unresolved ambiguity fails closed at
a Human Gate.

Comments are decision history, not startup context. Parent and Epic bodies are
upstream scope sources, not default execution inputs. Deterministic queries may
verify labels, state, Parent, blockers, Project Status, PR identity, SHAs, and
checks without injecting the corresponding full text into model context.

`feature-completion-audit` is the bounded exception: it may retrieve the target
Feature hierarchy and the state/evidence needed for that audit, but not
unrelated history or repository documentation.

Runtime token telemetry is outside this repository. Raw rollout logs and token
reports must not be committed and never change permissions, gates, findings,
verdicts, merge authorization, or completion evidence.

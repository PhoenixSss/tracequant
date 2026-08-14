# Removed and Disabled Legacy Paths

## Replaced normal paths

| Skill | Previous normal entry | Replacement |
| --- | --- | --- |
| Delivery | raw `workflow_evidence.py delivery-preflight` | fixed Evidence `delivery` |
| Delivery | raw `workflow_validation.py run --phase delivery` | fixed Validation `workflow-delivery --base-sha` |
| Delivery | raw `workflow_evidence.py delivery-readiness` | fixed Evidence `delivery-readiness` |
| Review | trusted raw Evidence/Validation tools | trusted fixed `evidence-runner` and `validation-runner` bundles |
| Closeout | raw `workflow_evidence.py closeout-plan/final` | fixed `closeout-readonly` plus exact snapshot `recheck` |
| Closeout | raw `workflow_validation.py run --phase closeout` | fixed `workflow-closeout --base-sha` |

The Skills no longer instruct the model to run the complete direct `gh` query chain or the direct CI-equivalent `uv` command sequence after a fixed runner succeeds. `skill_path_audit.py` fails when forbidden legacy fragments reappear.

## Responsibilities deliberately retained

- source-code and risk-focused reading;
- acceptance mapping and finding classification;
- targeted development commands that are not already covered by a named Runner profile;
- bounded inspection of a reported failure/unknown/drift result;
- explicit approval for push, GitHub writes, dangerous Git, and branch deletion;
- independent Review and manual Merge;
- exact branch and lifecycle checks during Closeout.

## Bounded fallback

Fallback is not a second success path. It is allowed only when the fixed runner is unavailable, incompatible, or identifies one precise missing fact. The Skill must record why the fallback was necessary, limit it to read-only targeted evidence, preserve partial/unknown status, and never repeat the entire legacy chain.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bug_policy import bug_contract_snapshot, is_valid_bug_contract
from documentation_policy import (
    documentation_contract_snapshot,
    is_valid_documentation_contract,
)
from research_policy import is_valid_research_contract, research_contract_snapshot
from workflow_common import is_sha
from workflow_evidence import _formal_blockers_gate

from .issue_profiles import LeafIssueKind, resolve_issue_profile
from .models import (
    LiveState,
    Phase,
    ResolutionStatus,
    _authoritative_remote_main_sha,
    _is_clean_current_main,
    _items,
)


@dataclass(frozen=True)
class PhaseDecision:
    phase: Phase
    eligible: bool
    reasons: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    issue_profile: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "capabilities": list(self.capabilities),
            "issue_profile": self.issue_profile,
        }


class PhaseEligibilityResolver:
    """Apply static phase capabilities to current live preconditions."""

    def resolve(
        self,
        state: LiveState,
        phase: Phase,
        *,
        owned_remediation_candidate: bool = False,
    ) -> PhaseDecision:
        reasons = list(state.stop_reasons)
        issue = state.issue
        relationships = state.relationships
        profile_resolution = resolve_issue_profile(
            issue if isinstance(issue, Mapping) else None
        )
        issue_profile = profile_resolution.to_dict()
        if state.status is not ResolutionStatus.RESOLVED:
            reasons.append("live state resolution stopped")
        if state.repository is None:
            reasons.append("repository identity is unavailable")
        if not isinstance(issue, Mapping):
            reasons.append("Task metadata is unavailable")
        else:
            profile = profile_resolution.profile
            if not profile_resolution.resolved:
                reasons.append(profile_resolution.error_message)
            elif profile is not None and not profile.lifecycle_enabled:
                reasons.append(profile_resolution.error_message)
            issue_state = str(issue.get("state", "")).upper()
            if phase is not Phase.CLOSEOUT and issue_state != "OPEN":
                reasons.append("Task is not OPEN")
            if phase is not Phase.CLOSEOUT:
                labels = set(_items(issue.get("labels")))
                lifecycle_labels = labels & {
                    "codex:needs-spec",
                    "codex:ready",
                    "codex:blocked",
                }
                if lifecycle_labels != {"codex:ready"}:
                    reasons.append(
                        "lifecycle labels must be exactly ['codex:ready']: "
                        f"{sorted(lifecycle_labels) or 'none'}"
                    )
            if profile_resolution.profile is not None and (
                profile_resolution.profile.issue_kind is LeafIssueKind.BUG
            ):
                bug_contract = issue.get("bug_contract")
                if not isinstance(bug_contract, Mapping):
                    body = issue.get("body")
                    bug_contract = bug_contract_snapshot(
                        body if isinstance(body, str) else None
                    )
                if not is_valid_bug_contract(bug_contract):
                    detail = bug_contract.get("detail") or "contract is invalid"
                    reasons.append(f"Bug defect contract invalid: {detail}")
            elif profile_resolution.profile is not None and (
                profile_resolution.profile.issue_kind is LeafIssueKind.DOCUMENTATION
            ):
                documentation_contract = issue.get("documentation_contract")
                if not isinstance(documentation_contract, Mapping):
                    body = issue.get("body")
                    documentation_contract = documentation_contract_snapshot(
                        body if isinstance(body, str) else None
                    )
                if not is_valid_documentation_contract(documentation_contract):
                    detail = (
                        documentation_contract.get("detail") or "contract is invalid"
                    )
                    reasons.append(f"Documentation contract invalid: {detail}")
            elif profile_resolution.profile is not None and (
                profile_resolution.profile.issue_kind is LeafIssueKind.RESEARCH
            ):
                research_contract = issue.get("research_contract")
                if not isinstance(research_contract, Mapping):
                    body = issue.get("body")
                    research_contract = research_contract_snapshot(
                        body if isinstance(body, str) else None
                    )
                if not is_valid_research_contract(research_contract):
                    detail = research_contract.get("detail") or "contract is invalid"
                    reasons.append(f"Research contract invalid: {detail}")
            project = issue.get("project_status")
            allowed_projects = {
                Phase.DELIVERY_PREPARE: {"Ready", "In Progress"},
                # A prior status write may have moved the Task to Review before
                # a later final verification stopped.  Allow the same LCK
                # Delivery Complete path to reacquire and safely finish that
                # partial invocation.
                Phase.DELIVERY_COMPLETE: {"Ready", "In Progress", "Review"},
                Phase.REVIEW_PREPARE: {"Review", "In Progress"},
                Phase.REVIEW_COMPLETE: {"Review", "In Progress"},
                Phase.REMEDIATION_PREPARE: {"Review"},
                Phase.REMEDIATION_NO_CHANGE: {"Review"},
                Phase.REMEDIATION_COMPLETE: {"Review"},
                Phase.CLOSEOUT: {
                    "Inbox",
                    "Specifying",
                    "Ready",
                    "In Progress",
                    "Review",
                    "Blocked",
                    "Done",
                },
            }[phase]
            if project not in allowed_projects:
                reasons.append("Project Status is unavailable or unknown")
            if phase in {Phase.DELIVERY_PREPARE, Phase.DELIVERY_COMPLETE}:
                if (
                    profile is not None
                    and profile.issue_kind is LeafIssueKind.TASK
                    and profile.requires_critical_outcome
                ):
                    critical = issue.get("critical_outcome")
                    if (
                        not isinstance(critical, Mapping)
                        or critical.get("status") != "valid"
                    ):
                        detail = (
                            critical.get("detail")
                            if isinstance(critical, Mapping)
                            else "contract unavailable"
                        )
                        reasons.append(f"Critical Outcome contract invalid: {detail}")

        downstream_contract = (
            state.task_contract if isinstance(state.task_contract, Mapping) else issue
        )
        blocker_gate = _formal_blockers_gate(
            relationships,
            downstream_contract=downstream_contract,
        )
        if blocker_gate.get("status") != "pass":
            detail = blocker_gate.get("detail") or "formal blocker gate did not pass"
            reasons.append(f"formal blocker gate: {detail}")

        capabilities: tuple[str, ...] = ()
        if phase is Phase.DELIVERY_PREPARE:
            if state.merged is True:
                reasons.append("Task already has a merged PR")
            if (
                state.local_task_branch is not None
                and state.git.get("branch") == state.local_task_branch
                and state.git.get("clean") is not True
            ):
                reasons.append("existing Task branch reuse requires a clean worktree")
            if state.local_task_head and state.remote_task_oid:
                if state.local_task_head != state.remote_task_oid:
                    reasons.append("Task branch has divergent local and remote tips")
            if state.open_pr is not None and state.local_task_head is not None:
                pr_head = state.open_pr.get("headRefOid")
                if is_sha(pr_head) and state.local_task_head != pr_head:
                    reasons.append(
                        "current OPEN PR head OID differs from local Task branch tip"
                    )
            if not state.local_task_branch and not state.remote_task_branch:
                if not _is_clean_current_main(state.git):
                    reasons.append(
                        "new workspace bootstrap requires clean main with "
                        "HEAD == local main == origin/main"
                    )
            capabilities = ("prepare_task_workspace",)
        elif phase is Phase.DELIVERY_COMPLETE:
            if state.merged is True:
                reasons.append("Task already has a merged PR")
            if state.local_task_branch is None:
                reasons.append("Delivery Complete requires a local Task branch")
            if state.git.get("branch") != state.target_branch:
                reasons.append(
                    "Delivery Complete requires the resolved Task branch selected"
                )
            if not is_sha(state.local_task_head):
                reasons.append("Delivery Complete requires a current local Task head")
            if state.open_pr is not None and state.open_pr.get("isDraft") is not False:
                reasons.append("Delivery Complete cannot continue with a Draft OPEN PR")
            if (
                profile_resolution.profile is not None
                and profile_resolution.profile.issue_kind is LeafIssueKind.DOCUMENTATION
            ):
                capabilities = (
                    "validate_documentation_candidate",
                    "run_formal_validation",
                    "commit_current_tree",
                    "ensure_remote_branch",
                    "ensure_open_pr",
                    "set_review_status",
                )
            elif (
                profile_resolution.profile is not None
                and profile_resolution.profile.issue_kind is LeafIssueKind.BUG
            ):
                capabilities = (
                    "validate_bug_contract",
                    "run_formal_validation",
                    "commit_current_tree",
                    "ensure_remote_branch",
                    "ensure_open_pr",
                    "set_review_status",
                )
            elif (
                profile_resolution.profile is not None
                and profile_resolution.profile.issue_kind is LeafIssueKind.RESEARCH
            ):
                capabilities = (
                    "validate_research_artifact",
                    "run_formal_validation",
                    "commit_current_tree",
                    "ensure_remote_branch",
                    "ensure_open_pr",
                    "set_review_status",
                )
            else:
                capabilities = (
                    "verify_critical_outcome",
                    "run_formal_validation",
                    "commit_current_tree",
                    "ensure_remote_branch",
                    "ensure_open_pr",
                    "set_review_status",
                )
        elif phase in {Phase.REVIEW_PREPARE, Phase.REVIEW_COMPLETE}:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            elif state.open_pr.get("isDraft") is not False:
                reasons.append(f"{phase.value} requires a non-Draft OPEN PR")
            if state.project_status not in {"Review", "In Progress"}:
                reasons.append(f"Task is not eligible for {phase.value}")
            capabilities = (
                ("prepare_read_only_review_context",)
                if phase is Phase.REVIEW_PREPARE
                else ("accept_semantic_review_verdict",)
            )
        elif phase in {
            Phase.REMEDIATION_PREPARE,
            Phase.REMEDIATION_NO_CHANGE,
            Phase.REMEDIATION_COMPLETE,
        }:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            elif state.open_pr.get("isDraft") is not False:
                reasons.append("Remediation requires a non-Draft OPEN PR")
            if state.project_status != "Review":
                reasons.append("Remediation requires Project Status Review")
            pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
            if not is_sha(pr_head):
                reasons.append("current OPEN PR head OID is unavailable")
            pr_base = state.open_pr.get("baseRefOid") if state.open_pr else None
            remote_main = _authoritative_remote_main_sha(state.git)
            if not is_sha(pr_base) or not is_sha(remote_main) or pr_base != remote_main:
                reasons.append("current OPEN PR base must match current origin/main")
            if state.remote_task_oid != pr_head:
                reasons.append("remote Task branch must match current OPEN PR head")
            if (
                state.local_task_head is not None
                and state.local_task_head != pr_head
                and not (
                    phase is Phase.REMEDIATION_COMPLETE and owned_remediation_candidate
                )
            ):
                reasons.append("local Task branch must match current OPEN PR head")
            if phase is Phase.REMEDIATION_PREPARE:
                capabilities = ("prepare_task_workspace", "load_review_findings")
            else:
                if state.local_task_branch is None:
                    reasons.append(f"{phase.value} requires a local Task branch")
                if state.git.get("branch") != state.target_branch:
                    reasons.append(
                        f"{phase.value} requires the resolved Task branch selected"
                    )
                if phase is Phase.REMEDIATION_NO_CHANGE:
                    capabilities = ("close_no_change_remediation",)
                elif (
                    profile_resolution.profile is not None
                    and profile_resolution.profile.issue_kind
                    is LeafIssueKind.DOCUMENTATION
                ):
                    capabilities = (
                        "validate_documentation_candidate",
                        "run_formal_validation",
                        "commit_current_tree",
                        "ensure_remote_branch",
                        "reuse_open_pr",
                    )
                elif (
                    profile_resolution.profile is not None
                    and profile_resolution.profile.issue_kind is LeafIssueKind.BUG
                ):
                    capabilities = (
                        "validate_bug_contract",
                        "run_formal_validation",
                        "commit_current_tree",
                        "ensure_remote_branch",
                        "reuse_open_pr",
                    )
                elif (
                    profile_resolution.profile is not None
                    and profile_resolution.profile.issue_kind is LeafIssueKind.RESEARCH
                ):
                    capabilities = (
                        "validate_research_artifact",
                        "run_formal_validation",
                        "commit_current_tree",
                        "ensure_remote_branch",
                        "reuse_open_pr",
                    )
                else:
                    capabilities = (
                        "verify_critical_outcome",
                        "run_formal_validation",
                        "commit_current_tree",
                        "ensure_remote_branch",
                        "reuse_open_pr",
                    )
        else:
            if state.merged is not True:
                reasons.append("Closeout requires one current merged PR")
            if state.open_pr is not None:
                reasons.append("Closeout cannot proceed while an OPEN PR exists")
            capabilities = ("resolve_cleanup_state",)
        return PhaseDecision(
            phase=phase,
            eligible=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            capabilities=capabilities,
            issue_profile=issue_profile,
        )

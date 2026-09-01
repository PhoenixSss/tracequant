from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from workflow_common import is_sha

from .issue_profiles import resolve_leaf_issue_profile
from .models import (
    LiveState,
    Phase,
    ResolutionStatus,
    _authoritative_remote_main_sha,
    _is_clean_current_main,
    _items,
)
from .profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyContext,
    ProfilePolicyRegistry,
    ProfileResolver,
    evaluate_profile_blockers,
    resolve_profile_policy,
    validate_profile_contract,
)
from .shared_facts import evaluate_shared_blockers


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

    def __init__(
        self,
        *,
        registry: ProfilePolicyRegistry | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self.registry = registry or DEFAULT_PROFILE_POLICY_REGISTRY
        self.profile_resolver = profile_resolver or resolve_leaf_issue_profile

    def blocker_reasons(
        self,
        state: LiveState,
        *,
        phase: Phase,
    ) -> tuple[str, ...]:
        """Aggregate generic relationship and registered policy blockers."""
        reasons: list[str] = []
        shared_gate = evaluate_shared_blockers(state.relationships)
        if shared_gate.get("status") != "pass":
            detail = shared_gate.get("detail") or "shared blocker gate did not pass"
            # Keep the established diagnostic prefix while making ownership
            # explicit: this is the generic aggregate, not profile semantics.
            reasons.append(f"formal blocker gate: {detail}")

        issue = state.issue
        profile_resolution = self.profile_resolver(
            issue if isinstance(issue, Mapping) else None
        )
        profile = profile_resolution.profile if profile_resolution.resolved else None
        target_contract: Mapping[str, Any] | None = None
        if isinstance(issue, Mapping):
            target_contract = dict(issue)
            if isinstance(state.task_contract, Mapping):
                target_contract.update(state.task_contract)
        downstream_contract = (
            state.task_contract if isinstance(state.task_contract, Mapping) else issue
        )
        validate_contract_at_entry = phase in {
            Phase.DELIVERY_PREPARE,
            Phase.DELIVERY_COMPLETE,
            Phase.REMEDIATION_PREPARE,
            Phase.REMEDIATION_COMPLETE,
        }
        if (
            profile is not None
            and isinstance(target_contract, Mapping)
            and (profile.contract_policy is not None or validate_contract_at_entry)
        ):
            try:
                contract_check = validate_profile_contract(
                    profile, target_contract, registry=self.registry
                )
                if not contract_check.valid:
                    reasons.append(contract_check.failure_reason)
                else:
                    policy_blockers = evaluate_profile_blockers(
                        profile,
                        target_contract,
                        contract_evidence=contract_check.evidence,
                        registry=self.registry,
                        context=PolicyContext(
                            profile=profile,
                            phase=phase.value,
                            issue=target_contract,
                            relationships=state.relationships,
                            repository=state.repository,
                            downstream_contract=downstream_contract,
                        ),
                    )
                    reasons.extend(
                        f"policy blocker [{blocker.code}]: {blocker.detail}"
                        for blocker in policy_blockers
                    )
            except ValueError as exc:
                reasons.append(f"profile blocker evaluation failed: {exc}")

        blocked_by = state.relationships.get("blocked_by")
        dependency_items = (
            blocked_by.get("items") if isinstance(blocked_by, Mapping) else None
        )
        if isinstance(dependency_items, list):
            for dependency in dependency_items:
                if not isinstance(dependency, Mapping):
                    continue
                if str(dependency.get("state", "")).upper() != "CLOSED":
                    continue
                labels = dependency.get("labels")
                labels_complete = dependency.get("labels_complete")
                if labels_complete is not None and labels_complete is not True:
                    reasons.append(
                        "formal blocker gate: closed dependency labels are incomplete"
                    )
                    continue
                if not isinstance(labels, list):
                    reasons.append(
                        "formal blocker gate: closed dependency labels are unavailable"
                    )
                    continue
                dependency_resolution = self.profile_resolver(dependency)
                dependency_profile = dependency_resolution.profile
                # Task Critical Outcome is a leaf-entry contract.  It is not a
                # dependency blocker policy; typed dependency semantics are
                # supplied only by the registered profile capability.
                if not dependency_resolution.resolved or dependency_profile is None:
                    reasons.append(
                        "formal blocker gate: closed dependency profile is unavailable"
                    )
                    continue
                if dependency_profile.contract_policy is None:
                    continue
                try:
                    dependency_blockers = evaluate_profile_blockers(
                        dependency_profile,
                        dependency,
                        registry=self.registry,
                        context=PolicyContext(
                            profile=dependency_profile,
                            phase=phase.value,
                            issue=dependency,
                            relationships=state.relationships,
                            repository=state.repository,
                            downstream_contract=downstream_contract,
                        ),
                    )
                except ValueError as exc:
                    reasons.append(f"profile blocker evaluation failed: {exc}")
                else:
                    reasons.extend(
                        f"policy blocker [{blocker.code}]: {blocker.detail}"
                        for blocker in dependency_blockers
                    )
        return tuple(dict.fromkeys(reasons))

    def resolve(
        self,
        state: LiveState,
        phase: Phase,
        *,
        owned_remediation_candidate: bool = False,
    ) -> PhaseDecision:
        reasons = list(state.stop_reasons)
        issue = state.issue
        profile_resolution = self.profile_resolver(
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
            elif profile is not None:
                try:
                    resolve_profile_policy(profile, registry=self.registry)
                except ValueError as exc:
                    reasons.append(f"profile policy resolution failed: {exc}")
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

        reasons.extend(self.blocker_reasons(state, phase=phase))

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
            candidate_capability = (
                profile_resolution.profile.candidate_capability
                if profile_resolution.profile is not None
                else "verify_critical_outcome"
            )
            capabilities = (
                candidate_capability,
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
                else:
                    candidate_capability = (
                        profile_resolution.profile.candidate_capability
                        if profile_resolution.profile is not None
                        else "verify_critical_outcome"
                    )
                    capabilities = (
                        candidate_capability,
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

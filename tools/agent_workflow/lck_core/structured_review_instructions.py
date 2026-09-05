"""Canonical semantic instructions for production Independent Review.

This module owns the reviewer-facing Structured Review v2 guidance.  It is
instruction data only: it does not create a completion gate, receipt protocol,
new verdict, or lifecycle state.
"""

from __future__ import annotations

from typing import Any, Final

from tracequant.contracts import ReviewSurface

STRUCTURED_REVIEW_INSTRUCTION_OWNER: Final[str] = (
    "tools/agent_workflow/lck_core/structured_review_instructions.py"
)
STRUCTURED_REVIEW_INSTRUCTION_VERSION: Final[str] = "v2"


_OBLIGATIONS: Final[tuple[dict[str, Any], ...]] = (
    {
        "obligation_id": "contract-critical-outcome",
        "instruction": (
            "Map the Objective, Requirements, Acceptance Criteria, Critical "
            "Outcome, constraints, and non-goals to the implementation and "
            "available evidence."
        ),
        "review_surfaces": (ReviewSurface.CONTRACT_CONFORMANCE.value,),
    },
    {
        "obligation_id": "functional-invariants",
        "instruction": (
            "Identify the relevant functional invariants and caller/callee "
            "assumptions, then construct applicable counterexamples."
        ),
        "review_surfaces": (ReviewSurface.FUNCTIONAL_CORRECTNESS.value,),
    },
    {
        "obligation_id": "boundary-conversion-error",
        "instruction": (
            "Enumerate applicable boundary, parsing, conversion, external-input, "
            "transport, exception, timeout, retry, malformed, missing, partial, "
            "and result-mapping outcomes."
        ),
        "review_surfaces": (ReviewSurface.ERROR_FAILURE_PATHS.value,),
    },
    {
        "obligation_id": "state-persistence-compatibility",
        "instruction": (
            "When applicable, inspect state transitions, persistence behavior, "
            "recovery and conflict paths, and Base-to-Head compatibility."
        ),
        "review_surfaces": (
            ReviewSurface.STATE_TRANSITIONS.value,
            ReviewSurface.PERSISTENCE_ATOMICITY.value,
            ReviewSurface.COMPATIBILITY_MIGRATION.value,
        ),
    },
    {
        "obligation_id": "tests-vs-claims",
        "instruction": (
            "Distinguish what tests and deterministic evidence actually prove "
            "from happy-path-only claims and remaining failure, boundary, state, "
            "compatibility, and invariant gaps."
        ),
        "review_surfaces": (ReviewSurface.TESTS_VS_CLAIMS.value,),
    },
    {
        "obligation_id": "adversarial-residual-sweep",
        "instruction": (
            "Before the final verdict, assume supported findings are fixed and "
            "search for independent root causes and residual risks across the "
            "applicable review surfaces."
        ),
        "review_surfaces": (ReviewSurface.FUNCTIONAL_CORRECTNESS.value,),
    },
)


def canonical_structured_review_instructions() -> dict[str, Any]:
    """Return the fresh reviewer handoff owned by this module."""
    return {
        "owner": STRUCTURED_REVIEW_INSTRUCTION_OWNER,
        "version": STRUCTURED_REVIEW_INSTRUCTION_VERSION,
        "obligations": [
            {
                **obligation,
                "review_surfaces": list(obligation["review_surfaces"]),
            }
            for obligation in _OBLIGATIONS
        ],
        "review_sequence": [
            "Complete every applicable obligation before the final verdict.",
            "A High or Medium blocker does not end semantic review; continue all "
            "remaining applicable surfaces.",
            "Perform the adversarial residual sweep before the existing final verdict.",
        ],
        "finding_guidance": [
            "Use the existing canonical finding/report semantics.",
            "When applicable, include affected location, violated contract or "
            "invariant, concrete failure scenario, supporting evidence, why "
            "current tests/evidence do not exclude it, and falsification reasoning.",
        ],
    }


__all__ = [
    "STRUCTURED_REVIEW_INSTRUCTION_OWNER",
    "STRUCTURED_REVIEW_INSTRUCTION_VERSION",
    "canonical_structured_review_instructions",
]

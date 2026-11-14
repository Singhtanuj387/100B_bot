from typing import Dict, Any, List
from loguru import logger
from langgraph.types import interrupt

from graph.state import CurationState
from core.db import AgentDatabase


def human_review_node(state: CurationState, db: AgentDatabase) -> Dict[str, Any]:
    """
    Human review node for borderline candidates (score 5.0 to 7.9).
    Uses LangGraph interrupt() to yield control to the caller (outside the graph).
    The outside caller displays ReviewUI and resumes with Command(resume=review_outcome).
    """
    borderline = list(state.get("borderline", []))
    approved = list(state.get("approved", []))
    rejected = list(state.get("rejected", []))
    dry_run = state.get("dry_run", False)
    target_date = state.get("target_date", "today")

    if not borderline:
        logger.info("No borderline videos pending review; proceeding directly.")
        return {
            "borderline": [],
            "approved": approved,
            "rejected": rejected,
        }

    # Check borderline policy: reject, approve_top, review
    # Default: review when interactive, approve_top when --no-review is used
    interactive = sys_stdin_is_interactive() and not state.get("no_review", False)
    policy = state.get("borderline_policy") or ("review" if interactive else "approve_top")
    target_count = int(state.get("target_count", 6))
    min_publish_score = float(state.get("min_publish_score", 6.5))

    logger.info(f"Borderline handling: {len(borderline)} clips | Policy: '{policy}' | Interactive: {interactive}")

    if policy == "reject":
        logger.info(f"Policy 'reject': Dropping all {len(borderline)} borderline candidates.")
        new_rejected = []
        for b in borderline:
            b["human_decision"] = "rejected"
            new_rejected.append(b)
            db.record_decision(
                b.get("post_id", "unknown"),
                b.get("file_hash", ""),
                float(b.get("score", 0.0)),
                "policy_rejected",
                "Dropped by borderline policy 'reject'",
                target_date,
            )
        return {
            "borderline": [],
            "approved": approved,
            "rejected": rejected + new_rejected,
            "human_interrupted": False,
        }

    if policy == "approve_top" or not interactive:
        # Fill remaining slots from the best borderline clips (still at or above min_publish_score)
        slots_remaining = max(0, target_count - len(approved))
        sorted_borderline = sorted(borderline, key=lambda x: float(x.get("score", 0.0)), reverse=True)

        new_approved = []
        new_rejected = []

        for b in sorted_borderline:
            score = float(b.get("score", 0.0))
            if len(new_approved) < slots_remaining and score >= min_publish_score:
                b["human_decision"] = "approved"
                b["status"] = "borderline_approved"
                b["caption_edited_by_human"] = False
                new_approved.append(b)
                db.record_decision(
                    b.get("post_id", "unknown"),
                    b.get("file_hash", ""),
                    score,
                    "unattended_approved",
                    f"Filled slot via borderline policy 'approve_top' (score {score:.2f} >= {min_publish_score})",
                    target_date,
                )
            else:
                b["human_decision"] = "rejected"
                new_rejected.append(b)
                db.record_decision(
                    b.get("post_id", "unknown"),
                    b.get("file_hash", ""),
                    score,
                    "unattended_rejected",
                    f"Borderline not selected under 'approve_top' (score {score:.2f})",
                    target_date,
                )

        logger.info(
            f"Policy 'approve_top': Promoted {len(new_approved)} clip(s) to approved (Score >= {min_publish_score}); "
            f"rejected {len(new_rejected)} clip(s)."
        )
        return {
            "borderline": [],
            "approved": approved + new_approved,
            "rejected": rejected + new_rejected,
            "human_interrupted": False,
        }

    # Policy 'review' (interactive mode): Interrupt graph execution and yield payload to external caller
    review_outcome = interrupt({
        "borderline": borderline,
        "auto_approved": approved,
        "auto_approved_count": len(approved),
    })

    # When resumed via Command(resume=review_outcome), process the user decisions
    new_approved = review_outcome.get("approved", []) if isinstance(review_outcome, dict) else []
    new_rejected = review_outcome.get("rejected", []) if isinstance(review_outcome, dict) else []

    for na in new_approved:
        db.record_decision(
            na.get("post_id", "unknown"),
            na.get("file_hash", ""),
            na.get("score", 6.0),
            "human_approved",
            "Approved in human review",
            target_date,
        )

    for nr in new_rejected:
        db.record_decision(
            nr.get("post_id", "unknown"),
            nr.get("file_hash", ""),
            nr.get("score", 6.0),
            "human_rejected",
            nr.get("reject_reason", "Human rejected"),
            target_date,
        )

    logger.info(f"Human review concluded: {len(new_approved)} approved, {len(new_rejected)} rejected.")

    return {
        "approved": approved + new_approved,
        "rejected": rejected + new_rejected,
        "borderline": [],
        "human_interrupted": False,
    }


def sys_stdin_is_interactive() -> bool:
    """Helper to check if terminal is running interactively."""
    import sys
    try:
        return sys.stdin.isatty()
    except Exception:
        return False

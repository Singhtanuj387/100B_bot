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

    # Handle unattended / non-interactive execution (--no-review or background cron)
    no_review = state.get("no_review", False) or not sys_stdin_is_interactive()
    if no_review:
        policy = state.get("borderline_policy", "dry_run_split" if dry_run else "reject")
        logger.info(
            f"[Unattended Mode --no-review] Resolving {len(borderline)} borderline candidate(s) via policy: '{policy}'"
        )
        new_approved = []
        new_rejected = []
        for b in borderline:
            score = float(b.get("score", 0))
            is_approved = (
                (policy == "dry_run_split" and score >= 6.5) or
                (policy == "approve_top" and score >= 7.0)
            )
            if is_approved:
                b["human_decision"] = "approved"
                b["caption_edited_by_human"] = False
                new_approved.append(b)
                db.record_decision(
                    b.get("post_id", "unknown"),
                    b.get("file_hash", ""),
                    score,
                    "unattended_approved",
                    "Auto-promoted borderline candidate in unattended mode",
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
                    "Auto-rejected borderline candidate in unattended mode",
                    target_date,
                )

        return {
            "borderline": [],
            "approved": approved + new_approved,
            "rejected": rejected + new_rejected,
            "human_interrupted": False,
        }

    # Interrupt graph execution and yield payload to external caller
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

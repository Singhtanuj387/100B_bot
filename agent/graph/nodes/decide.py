import math
from typing import Dict, Any, List
from loguru import logger
from graph.state import CurationState
from core.db import AgentDatabase


def normalize_engagement(raw_score: int) -> float:
    """Normalize raw upvotes/likes into a 1.0 to 10.0 scale."""
    if raw_score <= 10:
        return 1.0
    # Logarithmic scaling: 100 upvotes ~ 5.0, 1000 ~ 7.5, 10,000+ -> 10.0
    score = (math.log10(raw_score) / 4.0) * 10.0
    return min(10.0, max(1.0, round(score, 2)))


def decide_node(
    state: CurationState,
    config: Dict[str, Any],
    db: AgentDatabase,
) -> Dict[str, Any]:
    """
    Combines LLM score with engagement metrics to make routing decisions:
      - Auto-approve: >= 8.0
      - Auto-reject: < 5.0 or safety/watermark failure
      - Borderline: 5.0 - 7.9 -> Human review
    """
    candidates = state.get("candidates", [])
    approved = list(state.get("approved", []))
    borderline = list(state.get("borderline", []))
    rejected = list(state.get("rejected", []))
    judge_failed = list(state.get("judge_failed", []))
    target_date = state.get("target_date", "today")

    scoring_cfg = config.get("scoring", {})
    llm_weight = float(scoring_cfg.get("llm_weight", 0.70))
    eng_weight = float(scoring_cfg.get("engagement_weight", 0.30))
    auto_approve_thresh = float(scoring_cfg.get("auto_approve_threshold", 8.0))
    auto_reject_thresh = float(scoring_cfg.get("auto_reject_threshold", 5.0))

    logger.info(f"--- [Node: DECIDE] Routing {len(candidates)} evaluated candidates ---")

    for cand in candidates:
        post_id = cand.get("post_id", "unknown")
        file_hash = cand.get("file_hash", "")

        # 0. Skip judge-failed candidates completely:
        # Never route to borderline, never log as rejected decision, never update in registry.
        # Record for retry in the next run.
        if cand.get("judge_failed") or cand.get("status") == "judge_failed":
            logger.warning(
                f"Skipping {post_id} in decisions and registry: "
                f"judge_failed ({cand.get('judge_reason')})"
            )
            judge_failed.append(cand)
            try:
                db.record_judge_failed(cand)
            except Exception as e:
                logger.warning(f"Could not record judge_failed in db: {e}")
            continue

        # If candidate previously failed and is now successfully judged, clear retry record
        try:
            db.clear_judge_retry(post_id)
        except Exception:
            pass

        llm_score = float(cand.get("llm_score", 5.0))
        judge_reason = cand.get("judge_reason", "")

        # 1. Hard safety and quality gates
        if cand.get("is_broadcast_or_movie", False):
            reason = f"High copyright strike risk (TV/movie broadcast): {judge_reason}"
            logger.info(f"Rejected {post_id}: {reason}")
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": reason})
            db.record_decision(post_id, file_hash, llm_score, "rejected", reason, target_date)
            continue

        if not cand.get("is_safe_for_ads", True):
            reason = f"Unsafe for ads: {judge_reason}"
            logger.info(f"Rejected {post_id}: {reason}")
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": reason})
            db.record_decision(post_id, file_hash, llm_score, "rejected", reason, target_date)
            continue

        if cand.get("has_visible_watermark", False):
            reason = f"Visible competitor watermark: {judge_reason}"
            logger.info(f"Rejected {post_id}: {reason}")
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": reason})
            db.record_decision(post_id, file_hash, llm_score, "rejected", reason, target_date)
            continue

        if not cand.get("is_english_or_no_speech", True):
            reason = f"Critical non-English dialogue: {judge_reason}"
            logger.info(f"Rejected {post_id}: {reason}")
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": reason})
            db.record_decision(post_id, file_hash, llm_score, "rejected", reason, target_date)
            continue

        # 2. Hybrid scoring
        raw_eng = cand.get("score", 500)
        norm_eng = normalize_engagement(raw_eng)
        hybrid_score = round((llm_weight * llm_score) + (eng_weight * norm_eng), 2)
        cand["score"] = hybrid_score
        cand["reason"] = judge_reason

        # 3. Threshold routing
        if hybrid_score >= auto_approve_thresh:
            cand["status"] = "auto_approved"
            approved.append(cand)
            logger.success(f"Auto-approved {post_id} (Score: {hybrid_score:.1f})")
            db.record_decision(post_id, file_hash, hybrid_score, "auto_approved", judge_reason, target_date)

        elif hybrid_score < auto_reject_thresh:
            reason = f"Score too low ({hybrid_score:.1f} < {auto_reject_thresh}): {judge_reason}"
            logger.info(f"Auto-rejected {post_id}: {reason}")
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": reason})
            db.record_decision(post_id, file_hash, hybrid_score, "auto_rejected", reason, target_date)

        else:
            cand["status"] = "borderline"
            borderline.append(cand)
            logger.info(f"Borderline {post_id} (Score: {hybrid_score:.1f}) -> Human Review")
            db.record_decision(post_id, file_hash, hybrid_score, "borderline", judge_reason, target_date)

    logger.info(
        f"Decide complete -> Approved: {len(approved)} | Borderline: {len(borderline)} | "
        f"Rejected: {len(rejected)} | Judge Failed (Pending Retry): {len(judge_failed)}"
    )

    return {
        "approved": approved,
        "borderline": borderline,
        "rejected": rejected,
        "judge_failed": judge_failed,
        "candidates": [],  # Clear current candidate batch
    }


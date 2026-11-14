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
    Phase 3.6 Rank-Based Selection:
      - Filters hard safety gates (judge_failed, unsafe for ads, watermarks, non-English dialogue).
      - Computes hybrid score for every judged candidate.
      - Breaks ties by engagement score, then by recency (created_utc).
      - Auto-approves the top N (target_count) as long as score >= min_publish_score (default 6.5).
      - Routes clips with score from 5.0 to lowest chosen score to borderline pool.
      - Rejects clips below 5.0.
      - When fewer than target good clips meet the bar, schedules available clips without silently lowering quality.
    """
    candidates = state.get("candidates", [])
    approved = list(state.get("approved", []))
    borderline = list(state.get("borderline", []))
    rejected = list(state.get("rejected", []))
    judge_failed = list(state.get("judge_failed", []))
    target_date = state.get("target_date", "today")
    target_count = int(state.get("target_count", 6))

    scoring_cfg = config.get("scoring", {})
    llm_weight = float(scoring_cfg.get("llm_weight", 0.70))
    eng_weight = float(scoring_cfg.get("engagement_weight", 0.30))
    min_publish_score = float(scoring_cfg.get("min_publish_score", scoring_cfg.get("auto_approve_threshold", 6.5)))
    auto_reject_thresh = float(scoring_cfg.get("auto_reject_threshold", 5.0))

    logger.info(f"--- [Node: DECIDE] Rank-based routing for {len(candidates)} candidates (Target: {target_count}, Min Score: {min_publish_score}) ---")

    valid_candidates: List[Dict[str, Any]] = []

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

        # 1. Hard safety and quality gates (NEVER approved, whatever their score)
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
        raw_eng = int(cand.get("score", 500))
        norm_eng = normalize_engagement(raw_eng)
        hybrid_score = round((llm_weight * llm_score) + (eng_weight * norm_eng), 2)
        cand["score"] = hybrid_score
        cand["raw_engagement"] = raw_eng
        cand["reason"] = judge_reason
        cand["created_utc"] = float(cand.get("created_utc", 0.0))

        valid_candidates.append(cand)

    # 3. Rank valid candidates: sort descending by score, tie-break by engagement, then recency
    valid_candidates.sort(
        key=lambda c: (
            float(c.get("score", 0.0)),
            int(c.get("raw_engagement", 0)),
            float(c.get("created_utc", 0.0)),
        ),
        reverse=True,
    )

    slots_needed = max(0, target_count - len(approved))
    newly_approved: List[Dict[str, Any]] = []
    chosen_scores: List[float] = []

    # 4. Auto-approve top N as long as score >= min_publish_score
    for cand in valid_candidates:
        post_id = cand.get("post_id", "unknown")
        file_hash = cand.get("file_hash", "")
        h_score = float(cand.get("score", 0.0))

        if len(newly_approved) < slots_needed and h_score >= min_publish_score:
            cand["status"] = "auto_approved"
            newly_approved.append(cand)
            chosen_scores.append(h_score)
            logger.success(f"Auto-approved top clip {post_id} (Rank #{len(approved) + len(newly_approved)}, Score: {h_score:.2f})")
            db.record_decision(post_id, file_hash, h_score, "auto_approved", cand.get("reason", ""), target_date)

    approved.extend(newly_approved)

    # 5. Route remaining candidates: [5.0, lowest_chosen_score) -> Borderline, < 5.0 -> Rejected
    lowest_chosen_score = min(chosen_scores) if chosen_scores else min_publish_score

    for cand in valid_candidates:
        if cand in newly_approved:
            continue

        post_id = cand.get("post_id", "unknown")
        file_hash = cand.get("file_hash", "")
        h_score = float(cand.get("score", 0.0))
        reason = cand.get("reason", "")

        if h_score >= auto_reject_thresh:
            cand["status"] = "borderline"
            borderline.append(cand)
            logger.info(f"Borderline {post_id} (Score: {h_score:.2f} < lowest chosen {lowest_chosen_score:.2f}) -> Borderline Pool")
            db.record_decision(post_id, file_hash, h_score, "borderline", reason, target_date)
        else:
            rej_reason = f"Score too low ({h_score:.2f} < {auto_reject_thresh}): {reason}"
            cand["status"] = "auto_rejected"
            rejected.append({"post_id": post_id, "title": cand.get("title"), "reason": rej_reason})
            logger.info(f"Auto-rejected {post_id}: {rej_reason}")
            db.record_decision(post_id, file_hash, h_score, "auto_rejected", rej_reason, target_date)

    if len(approved) < target_count:
        logger.warning(
            f"Fewer than {target_count} good clips met quality threshold ({len(approved)}/{target_count} >= {min_publish_score}). "
            "Scheduling available clips without lowering quality bar silently."
        )

    logger.info(
        f"Decide complete -> Approved: {len(approved)} (Daily Top) | Borderline: {len(borderline)} | "
        f"Rejected: {len(rejected)} | Judge Failed (Pending Retry): {len(judge_failed)}"
    )

    return {
        "approved": approved,
        "borderline": borderline,
        "rejected": rejected,
        "judge_failed": judge_failed,
        "candidates": [],  # Clear current candidate batch
    }


from datetime import datetime
from typing import Dict, Any, List
from loguru import logger

from core.rights import RightsCheckEngine
from graph.state import CurationState


def rights_check_node(
    state: CurationState,
    config: Dict[str, Any],
    db: Any,
) -> Dict[str, Any]:
    """
    LangGraph Rights Check Node.
    Executes 5-layer Content ID and agency risk check on all approved and borderline clips.
    Blocks risky candidates before scheduling, allowing existing planning loops and backfill
    to replenish any shortage.
    """
    logger.info("--- Starting Rights Check Node (5 Layers) ---")

    engine = RightsCheckEngine(config=config, db=db)
    target_date = state.get("target_date") or datetime.now().strftime("%Y-%m-%d")

    approved = list(state.get("approved", []))
    borderline = list(state.get("borderline", []))
    rejected = list(state.get("rejected", []))
    rights_evaluations = list(state.get("rights_evaluations", []))

    new_approved = []
    new_borderline = []
    blocked_count = 0

    # 1. Evaluate Approved clips
    for clip in approved:
        post_id = clip.get("post_id", "")
        video_path = clip.get("video_path") or clip.get("edited_path") or ""
        raw_video_path = clip.get("raw_video_path") or video_path
        title = clip.get("title", "")
        uploader = clip.get("uploader", "")
        subreddit = clip.get("subreddit") or clip.get("source_name") or ""
        source_url = clip.get("source_url") or clip.get("post_url") or ""
        duration = clip.get("duration")

        eval_res = engine.evaluate_clip(
            post_id=post_id,
            video_path=video_path,
            raw_video_path=raw_video_path,
            title=title,
            uploader=uploader,
            subreddit=subreddit,
            source_url=source_url,
            target_date=target_date,
            duration=duration,
        )
        rights_evaluations.append(eval_res)

        decision = eval_res["decision"]
        risk_score = eval_res["risk_score"]
        reasons = eval_res["reasons"]

        clip["rights_risk"] = risk_score
        clip["rights_status"] = "needs_check" if decision == "review" else "cleared"

        if decision == "block":
            blocked_count += 1
            primary_reason = reasons[0] if reasons else "rights_agency"
            logger.warning(f"Clip {post_id} BLOCKED by rights check: {primary_reason} (Score: {risk_score})")

            clip["decision_reason"] = primary_reason
            clip["status"] = "rejected"
            rejected.append(clip)

            # Log to agent_decisions
            if db and hasattr(db, "log_decision"):
                try:
                    db.log_decision(
                        post_id=post_id,
                        file_hash=clip.get("file_hash"),
                        hybrid_score=clip.get("hybrid_score", 0.0),
                        status="rejected",
                        decision_reason=primary_reason,
                        target_date=target_date,
                    )
                except Exception as e:
                    logger.debug(f"Failed to log blocked decision: {e}")
        else:
            new_approved.append(clip)

    # 2. Evaluate Borderline clips
    for clip in borderline:
        post_id = clip.get("post_id", "")
        video_path = clip.get("video_path") or clip.get("edited_path") or ""
        raw_video_path = clip.get("raw_video_path") or video_path
        title = clip.get("title", "")
        uploader = clip.get("uploader", "")
        subreddit = clip.get("subreddit") or clip.get("source_name") or ""
        source_url = clip.get("source_url") or clip.get("post_url") or ""
        duration = clip.get("duration")

        eval_res = engine.evaluate_clip(
            post_id=post_id,
            video_path=video_path,
            raw_video_path=raw_video_path,
            title=title,
            uploader=uploader,
            subreddit=subreddit,
            source_url=source_url,
            target_date=target_date,
            duration=duration,
        )
        rights_evaluations.append(eval_res)

        decision = eval_res["decision"]
        risk_score = eval_res["risk_score"]
        reasons = eval_res["reasons"]

        clip["rights_risk"] = risk_score
        clip["rights_status"] = "needs_check" if decision == "review" else "cleared"

        if decision == "block":
            blocked_count += 1
            primary_reason = reasons[0] if reasons else "rights_agency"
            logger.warning(f"Borderline clip {post_id} BLOCKED by rights check: {primary_reason}")

            clip["decision_reason"] = primary_reason
            clip["status"] = "rejected"
            rejected.append(clip)

            if db and hasattr(db, "log_decision"):
                try:
                    db.log_decision(
                        post_id=post_id,
                        file_hash=clip.get("file_hash"),
                        hybrid_score=clip.get("hybrid_score", 0.0),
                        status="rejected",
                        decision_reason=primary_reason,
                        target_date=target_date,
                    )
                except Exception as e:
                    logger.debug(f"Failed to log blocked decision: {e}")
        else:
            new_borderline.append(clip)

    logger.info(
        f"Rights Check completed: {len(new_approved)} approved, "
        f"{len(new_borderline)} borderline, {blocked_count} blocked by rights."
    )

    return {
        "approved": new_approved,
        "borderline": new_borderline,
        "rejected": rejected,
        "rights_evaluations": rights_evaluations,
    }

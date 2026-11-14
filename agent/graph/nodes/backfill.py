from typing import Dict, Any, List
from loguru import logger
from graph.state import CurationState
from core.db import AgentDatabase


def backfill_node(state: CurationState, db: AgentDatabase) -> Dict[str, Any]:
    """
    Backfills approved videos from the SQLite reserve pool if approved < target_count.
    If the reserve pool is insufficient to cover the shortage, triggers one extra loop
    through the PLAN node.

    State changes (approved, backfill_performed, trigger_extra_loop) are returned as
    explicit node outputs so LangGraph persists them across node boundaries.
    """
    if state.get("human_interrupted", False):
        return {}

    approved = list(state.get("approved", []))
    target_count = state.get("target_count", 24)
    shortage = target_count - len(approved)

    # If already performed backfill in a prior pass, do not backfill or trigger extra loop again
    if state.get("backfill_performed", False):
        logger.info(
            f"--- [Node: BACKFILL] Already performed in earlier loop. "
            f"Final approved count: {len(approved)}/{target_count} ---"
        )
        return {
            "trigger_extra_loop": False,
        }

    logger.info(f"--- [Node: BACKFILL] Current approved videos: {len(approved)}/{target_count} ---")

    if shortage > 0:
        logger.warning(f"Shortage of {shortage} clips. Attempting reserve pool backfill...")
        reserves = db.fetch_reserve_pool_candidates(limit=shortage)
        if reserves:
            logger.info(f"Backfilling {len(reserves)} videos from reserve pool.")
            for r in reserves:
                r["status"] = "reserve_backfill"
                r["is_reserve_backfill"] = True
                r["already_edited"] = True
                r["edited_video_path"] = r.get("video_path")
                if "title" in r and not r.get("youtube_title"):
                    r["youtube_title"] = r["title"]
                if "description" in r and not r.get("youtube_description"):
                    r["youtube_description"] = r["description"]
                approved.append(r)
            db.mark_reserve_used([r["post_id"] for r in reserves])

        remaining_shortage = target_count - len(approved)
        if remaining_shortage > 0:
            logger.info(
                f"Reserve pool insufficient (still short {remaining_shortage}). "
                "Triggering one final extra loop through PLAN."
            )
            return {
                "approved": approved,
                "backfill_performed": True,
                "trigger_extra_loop": True,
            }
        else:
            logger.info(f"Target count satisfied after reserve backfill ({len(approved)}/{target_count}).")
            return {
                "approved": approved,
                "backfill_performed": True,
                "trigger_extra_loop": False,
            }

    logger.info("Target count satisfied. No reserve backfill needed.")
    return {
        "approved": approved,
        "backfill_performed": True,
        "trigger_extra_loop": False,
    }


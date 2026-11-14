from typing import Dict, Any, List, Optional
from loguru import logger

from graph.state import CurationState
from core.db import AgentDatabase


def update_registry_node(
    state: CurationState,
    db: AgentDatabase,
) -> Dict[str, Any]:
    """
    Phase 3.5: Post-Decision Registry Update Node.
    Runs strictly AFTER final decisions (auto-approval, borderline review, and backfill).
    Traces every video back to its (source_type, source_name, listing_mode) via meta.json
    and updates cumulative yield metrics, Bayesian smoothed rates, and promotion/pause status.
    """
    logger.info("--- [Node: UPDATE_REGISTRY] Updating Source Registry yields post-decision ---")
    target_date = state.get("target_date", "")

    approved_list = state.get("approved", [])
    rejected_list = state.get("rejected", [])

    # Group metrics by (source_type, source_name, listing_mode)
    source_stats: Dict[tuple, Dict[str, Any]] = {}

    def _extract_source_key(video_item: Dict[str, Any]) -> tuple:
        meta = video_item.get("meta", {})
        extra = video_item.get("extra", {}) or meta.get("extra", {})

        s_type = meta.get("source_type") or extra.get("source_type") or "subreddit"
        s_name = meta.get("source_name") or extra.get("source_name") or extra.get("subreddit")
        if not s_name or s_name.lower() == "reddit":
            raw_s = video_item.get("source", "funny")
            s_name = "funny" if raw_s.lower() == "reddit" else raw_s
        s_mode = meta.get("listing_mode") or extra.get("listing_mode") or extra.get("listing", "hot")
        return (s_type, s_name, s_mode)

    # 1. Process Approved Clips
    for v in approved_list:
        if v.get("judge_failed") or v.get("status") == "judge_failed":
            continue
        if v.get("is_reserve_backfill") or v.get("status") == "reserve_backfill":
            logger.debug(f"Skipping backfilled reserve clip {v.get('post_id')} from registry metrics.")
            continue
        key = _extract_source_key(v)
        if key not in source_stats:
            source_stats[key] = {
                "downloaded": 0,
                "approved": 0,
                "rejected": 0,
                "reasons": {},
                "cursor": None,
            }
        source_stats[key]["downloaded"] += 1
        source_stats[key]["approved"] += 1

        # Hot listings must never store or update pagination cursors
        if key[2] != "hot":
            cursor = (v.get("extra") or {}).get("pagination_cursor") or (v.get("meta", {}).get("extra", {})).get("pagination_cursor")
            if cursor:
                source_stats[key]["cursor"] = cursor

    # 2. Process Rejected Clips
    for v in rejected_list:
        if v.get("judge_failed") or v.get("status") == "judge_failed":
            continue
        if v.get("is_reserve_backfill") or v.get("status") == "reserve_backfill":
            continue
        key = _extract_source_key(v)
        if key not in source_stats:
            source_stats[key] = {
                "downloaded": 0,
                "approved": 0,
                "rejected": 0,
                "reasons": {},
                "cursor": None,
            }
        source_stats[key]["downloaded"] += 1
        source_stats[key]["rejected"] += 1

        reason = v.get("reason") or v.get("decision_reason") or "unspecified"
        source_stats[key]["reasons"][reason] = source_stats[key]["reasons"].get(reason, 0) + 1

        # Hot listings must never store or update pagination cursors
        if key[2] != "hot":
            cursor = (v.get("extra") or {}).get("pagination_cursor") or (v.get("meta", {}).get("extra", {})).get("pagination_cursor")
            if cursor:
                source_stats[key]["cursor"] = cursor

    # 3. Update Database for each source
    for (s_type, s_name, s_mode), data in source_stats.items():
        cursor_to_save = data["cursor"] if s_mode != "hot" else None
        db.update_source_metrics(
            source_type=s_type,
            name=s_name,
            listing_mode=s_mode,
            found=data["downloaded"],
            downloaded=data["downloaded"],
            approved=data["approved"],
            rejected=data["rejected"],
            reject_reasons=data["reasons"],
            cursor=cursor_to_save,
            run_date=target_date,
        )
        logger.info(
            f"Registry updated ({s_type}, {s_name}, {s_mode}): "
            f"+{data['approved']} approved, +{data['rejected']} rejected"
        )

    return {}

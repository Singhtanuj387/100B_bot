from typing import Dict, Any, List, Optional
from loguru import logger
from graph.state import CurationState
from llm.schemas import PlanResult
from llm.prompts import build_plan_system_prompt, build_plan_user_prompt


def plan_node(
    state: CurationState,
    config: Dict[str, Any],
    llm_client: Any,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Decide where to discover videos today.
    Phase 3.5:
      - Uses Source Registry with ~80% active / ~20% trial bandit selection.
      - Loop 1 picks top sources from registry.
      - Loop 2+ adapts by selecting next best sources from registry without extra discovery calls.
    """
    loop_count = state.get("loop_count", 0) + 1
    target_count = state.get("target_count", 24)
    cfg_buffer = state.get("spare_buffer", 4)
    buffer = min(cfg_buffer, target_count // 4) if target_count >= 4 else 0
    target_needed = max(1, (target_count + buffer) - len(state.get("approved", [])) - len(state.get("borderline", [])))

    allowed_sources = config.get("allowed_sources", {})
    allowed_subs = allowed_sources.get("subreddits", ["funny", "Unexpected", "MadeMeSmile", "AnimalsBeingDerps"])
    twitter_enabled = allowed_sources.get("twitter_enabled", False)
    allowed_twit = allowed_sources.get("twitter_accounts", ["DailyViralVids", "LaughsDaily"]) if twitter_enabled else []
    sources_tried = list(state.get("sources_tried", []))

    logger.info(f"--- [Node: PLAN] Loop #{loop_count} | Target needed: {target_needed} clips ---")

    # If database is provided, use 80/20 Source Registry bandit selection
    selected_from_registry: List[Dict[str, Any]] = []
    if db and hasattr(db, "get_sources_for_selection"):
        try:
            selected_from_registry = db.get_sources_for_selection(
                total_budget=8,
                active_ratio=0.8,
                trial_ratio=0.2,
            )
            reg_subs = [s["name"] for s in selected_from_registry if s["source_type"] == "subreddit"]
            if reg_subs:
                allowed_subs = reg_subs
        except Exception as e:
            logger.warning(f"Error querying sources from registry: {e}")

    if loop_count == 1:
        initial_subs = allowed_subs[:4]
        initial_twit = []  # X is experimental; test Reddit-only first
        plan = {
            "subreddits": initial_subs,
            "twitter_accounts": initial_twit,
            "strategy_reason": "80/20 Registry selection (top active by yield + trial sources).",
            "target_to_fetch": target_needed if target_needed == 1 else int(target_needed * 1.5),
            "proposed_new_sources": [],
        }
        for s in initial_subs:
            if s not in sources_tried:
                sources_tried.append(s)
        for t in initial_twit:
            if t not in sources_tried:
                sources_tried.append(t)

        return {
            "plan": plan,
            "selected_sources": selected_from_registry,
            "sources_tried": sources_tried,
            "loop_count": loop_count,
            "trigger_extra_loop": False,
        }

    # Loop 2+: LLM-guided adaptation
    # Aggregate rejection reasons
    rejections = state.get("rejected", [])
    reject_counts: Dict[str, int] = {}
    for r in rejections:
        reason = r.get("reason", "unknown")
        reject_counts[reason] = reject_counts.get(reason, 0) + 1

    sys_prompt = build_plan_system_prompt(allowed_subs, allowed_twit)
    usr_prompt = build_plan_user_prompt(loop_count, target_needed, reject_counts, sources_tried)

    try:
        plan_res: PlanResult = llm_client.call_structured(
            task_type="plan",
            system_prompt=sys_prompt,
            user_prompt=usr_prompt,
            schema=PlanResult,
        )
        # Validate that selected sources are in allowed list
        safe_subs = [s for s in plan_res.subreddits if s in allowed_subs]
        safe_twit = [t for t in plan_res.twitter_accounts if t in allowed_twit]

        # If LLM selected nothing valid from allowed list, pick untried allowed sources
        if not safe_subs:
            untried = [s for s in allowed_subs if s not in sources_tried]
            safe_subs = untried[:3] if untried else allowed_subs[:3]

        plan = {
            "subreddits": safe_subs,
            "twitter_accounts": safe_twit,
            "strategy_reason": plan_res.strategy_reason,
            "target_to_fetch": target_needed * 2,
            "proposed_new_sources": plan_res.proposed_new_sources,
        }
        for s in safe_subs + safe_twit:
            if s not in sources_tried:
                sources_tried.append(s)

        logger.info(f"Adaptive plan created: {safe_subs} + {safe_twit} ({plan_res.strategy_reason})")
        return {
            "plan": plan,
            "selected_sources": selected_from_registry,
            "sources_tried": sources_tried,
            "loop_count": loop_count,
            "trigger_extra_loop": False,
        }

    except Exception as e:
        logger.error(f"Plan LLM failed: {e}. Falling back to untried allowed sources.")
        untried = [s for s in allowed_subs if s not in sources_tried]
        fallback_subs = untried[:3] if untried else allowed_subs[:3]
        return {
            "plan": {
                "subreddits": fallback_subs,
                "twitter_accounts": allowed_twit[:1] if allowed_twit else [],
                "strategy_reason": "Fallback to untried allowed sources.",
                "target_to_fetch": target_needed * 2,
                "proposed_new_sources": [],
            },
            "selected_sources": selected_from_registry,
            "sources_tried": sources_tried + fallback_subs,
            "loop_count": loop_count,
            "trigger_extra_loop": False,
        }

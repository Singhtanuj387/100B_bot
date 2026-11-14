import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from loguru import logger

from llm.client import LLMClient
from llm.schemas import TitlePrescreenResult
from llm.prompts import (
    build_title_prescreen_system_prompt,
    build_title_prescreen_user_prompt,
)


def prescreen_titles(
    candidates: List[Dict[str, Any]],
    llm_client: LLMClient,
    drops_log_path: Optional[Path] = None,
    batch_size: int = 30,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Cautious text-only LLM pre-screen.
    Batches up to 30 titles per call using cheap text LLM (e.g. gemini-3.1-flash-lite).
    Filters out obvious non-comedy (news, politics, Q&A, text memes) BEFORE downloading media.
    CRITICAL: Never drops vague comedy titles (e.g. 'Maybe Maybe Maybe').
    All dropped items are recorded to drops_log_path for auditability.
    """
    if not candidates:
        return [], []

    kept_candidates: List[Dict[str, Any]] = []
    dropped_candidates: List[Dict[str, Any]] = []

    # Prepare log file
    resolved_drops_log = drops_log_path or (Path("logs") / "title_prescreen_drops.jsonl")
    resolved_drops_log.parent.mkdir(parents=True, exist_ok=True)

    cand_by_id = {c["post_id"]: c for c in candidates}

    # Process in batches
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        sys_prompt = build_title_prescreen_system_prompt()
        user_prompt = build_title_prescreen_user_prompt(batch)

        try:
            res: TitlePrescreenResult = llm_client.generate_structured(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                response_schema=TitlePrescreenResult,
                task_type="title_prescreen",
            )

            decision_map = {d.post_id: d for d in res.decisions}
            for c in batch:
                p_id = c["post_id"]
                decision = decision_map.get(p_id)

                if decision and not decision.keep:
                    # Dropped by pre-screen
                    drop_info = {
                        "post_id": p_id,
                        "title": c.get("title", ""),
                        "source": c.get("source", ""),
                        "subreddit": c.get("extra", {}).get("subreddit", ""),
                        "drop_reason": decision.drop_reason or "non_comedy",
                        "timestamp": datetime.now().isoformat(),
                    }
                    dropped_candidates.append(drop_info)
                    logger.info(
                        f"Title Pre-screen DROPPED [{p_id}] '{c.get('title', '')[:40]}' - Reason: {drop_info['drop_reason']}"
                    )
                    with open(resolved_drops_log, "a", encoding="utf-8") as f:
                        f.write(json.dumps(drop_info) + "\n")
                else:
                    # Kept (either explicitly approved or defaulted if omitted)
                    kept_candidates.append(c)

        except Exception as e:
            logger.warning(
                f"Error during title pre-screen batch: {e}. Defaulting to KEEP all {len(batch)} candidates in batch."
            )
            # Fail-safe: never drop candidates due to an LLM evaluation error
            kept_candidates.extend(batch)

    logger.info(
        f"Title Pre-screen complete: {len(kept_candidates)} kept, {len(dropped_candidates)} dropped"
    )
    return kept_candidates, dropped_candidates

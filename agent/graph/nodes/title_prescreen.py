from typing import Dict, Any, List
from loguru import logger

from graph.state import CurationState
from llm.client import LLMClient
from llm.title_prescreen import prescreen_titles


def title_prescreen_node(
    state: CurationState,
    config: Dict[str, Any],
    llm_client: LLMClient,
) -> Dict[str, Any]:
    """
    Phase 3.5: Cautious text-only Title Pre-Screen.
    Evaluates candidate titles BEFORE downloading media files.
    Batches up to 30 titles per call using cheap text LLM (e.g. gemini-3.1-flash-lite).
    Never drops vague comedy titles (e.g. 'Maybe Maybe Maybe').
    """
    raw_candidates = state.get("raw_candidates", [])
    logger.info(f"--- [Node: TITLE_PRESCREEN] Pre-screening {len(raw_candidates)} candidate titles ---")

    if not raw_candidates:
        return {"vetted_candidates": [], "dropped_titles": []}

    kept, dropped = prescreen_titles(
        candidates=raw_candidates,
        llm_client=llm_client,
        batch_size=30,
    )

    logger.info(
        f"Title Pre-screen approved {len(kept)} candidates for download, dropped {len(dropped)} non-comedy."
    )
    return {
        "vetted_candidates": kept,
        "dropped_titles": dropped,
        "cost_so_far": llm_client.total_cost_usd,
        "llm_calls_count": llm_client.call_count,
    }

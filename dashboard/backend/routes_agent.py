from fastapi import APIRouter, Query, HTTPException
from dashboard.backend.db import get_agent_days, get_agent_day_detail

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/days")
def agent_days(days: int = Query(default=7, ge=1, le=30)):
    """
    Return last `days` summaries for the Day picker cards.
    Shows found, rejected, accepted, and is_running indicator.
    """
    return get_agent_days(num_days=days)


@router.get("/day/{date}")
def agent_day_detail(date: str):
    """
    Return full day detail: funnel ribbon, totals, tomorrow lineup,
    reserve pool rows, rejection reasons, and sources.
    """
    if not date:
        raise HTTPException(status_code=400, detail="Missing date parameter")
    return get_agent_day_detail(date)

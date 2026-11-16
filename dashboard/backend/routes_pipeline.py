from fastapi import APIRouter
from dashboard.backend.db import get_pipeline_status

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/status")
def pipeline_status():
    """Return pipeline last run status and next run time."""
    return get_pipeline_status()

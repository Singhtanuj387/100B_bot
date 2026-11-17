from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dashboard.backend.routes_pipeline import router as pipeline_router
from dashboard.backend.routes_agent import router as agent_router
from dashboard.backend.routes_schedule import router as schedule_router
from dashboard.backend.routes_youtube import router as youtube_router
from dashboard.backend.routes_rights import router as rights_router
from dashboard.backend.db import WORKSPACE_DIR

FRONTEND_DIST = WORKSPACE_DIR / "dashboard" / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="100B Local Pipeline Dashboard",
        docs_url=None,  # Disabled for security
        redoc_url=None,
    )

    # Security Middleware: verify incoming Host is strictly localhost / 127.0.0.1
    @app.middleware("http")
    async def enforce_localhost_only(request: Request, call_next):
        host = request.headers.get("host", "").lower().split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "Access denied. Dashboard is bound strictly to 127.0.0.1."},
            )
        response = await call_next(request)
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    # Include API Routers
    app.include_router(pipeline_router)
    app.include_router(agent_router)
    app.include_router(schedule_router)
    app.include_router(youtube_router)
    app.include_router(rights_router)

    @app.get("/api/health")
    def health_check():
        return {"status": "ok", "app": "100B Dashboard"}

    # Mount static assets if frontend has been built
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Client-side SPA fallback for all frontend views
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        index_file = FRONTEND_DIST / "index.html"
        if index_file.is_file():
            # Check if specific static file exists in dist
            target = FRONTEND_DIST / full_path
            if target.is_file() and not full_path.endswith(".html"):
                return FileResponse(target)
            return FileResponse(index_file)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "100B Dashboard Backend Active. Build frontend into dashboard/frontend/dist to view UI."},
        )

    return app


app = create_app()

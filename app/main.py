from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.request_limits import RequestBodyLimitMiddleware
from app.services.job_service import JobService

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_directories()
        try:
            yield
        finally:
            app.state.jobs.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Prompt → validated parametric CAD DSL → CadQuery/OpenSCAD artifacts",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.jobs = JobService(settings)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        path="/api/v1/image-analysis",
        max_body_bytes=settings.max_image_bytes + 131_072,
        max_concurrency=settings.image_analysis_concurrency,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        path="/api/v1/dxf-analysis",
        max_body_bytes=settings.max_dxf_bytes + 131_072,
        max_concurrency=settings.dxf_analysis_concurrency,
    )
    for feature_tree_path in (
        "/api/v1/image-feature-tree-to-spec",
        "/api/v1/generate-from-image-feature-tree",
        "/api/v1/dxf-feature-tree-to-spec",
        "/api/v1/generate-from-dxf-feature-tree",
    ):
        app.add_middleware(
            RequestBodyLimitMiddleware,
            path=feature_tree_path,
            max_body_bytes=settings.max_feature_tree_body_bytes,
            max_concurrency=settings.feature_tree_concurrency,
        )
    for generation_path in (
        "/api/v1/plan",
        "/api/v1/generate",
        "/api/v1/generate-from-spec",
        "/api/v1/validate",
    ):
        app.add_middleware(
            RequestBodyLimitMiddleware,
            path=generation_path,
            max_body_bytes=settings.max_generate_body_bytes,
            max_concurrency=settings.generate_concurrency,
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )
    app.include_router(router)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()

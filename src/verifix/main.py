"""
FastAPI application factory.

Run locally:  uvicorn verifix.main:app --reload --port 8010
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from verifix import __version__
from verifix.api import evals, github, health, repos, tasks
from verifix.core.config import Settings, get_settings
from verifix.core.db import dispose_engine
from verifix.core.logging import configure_logging, get_logger
from verifix.core.redact import register_secret
from verifix.wiring import build_llm, build_manager, build_sandbox

__all__ = ["app", "build_llm", "build_manager", "build_sandbox", "create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if settings.anthropic_api_key is not None:
        # Exact-value redaction for our own key, on top of the format patterns.
        register_secret(settings.anthropic_api_key.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        get_logger("verifix").info(
            "startup",
            version=__version__,
            env=settings.env,
            anthropic="configured" if settings.anthropic_configured else "missing",
        )
        manager = getattr(app.state, "manager", None)
        if manager is None:
            manager = build_manager(settings)
            app.state.manager = manager
            try:
                recovered = await manager.recover_interrupted()
                if recovered:
                    get_logger("verifix").warning(
                        "marked interrupted tasks as failed", count=recovered
                    )
            except Exception:
                get_logger("verifix").warning("could not check for interrupted tasks")
        yield
        await manager.shutdown()
        await dispose_engine()

    app = FastAPI(title="verifix", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(health.router)
    app.include_router(repos.router)
    app.include_router(tasks.router)
    app.include_router(evals.router)
    app.include_router(github.router)
    return app


app = create_app()

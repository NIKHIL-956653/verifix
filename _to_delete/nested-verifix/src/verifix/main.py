"""
FastAPI application factory.

Run locally:  uvicorn verifix.main:app --reload --port 8010
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from verifix import __version__
from verifix.agent.loop import AgentLoop
from verifix.agent.manager import TaskManager
from verifix.agent.store import TaskStore
from verifix.api import health, repos, tasks
from verifix.core.config import Settings, get_settings
from verifix.core.db import dispose_engine, get_sessionmaker
from verifix.core.logging import configure_logging, get_logger
from verifix.core.redact import register_secret
from verifix.llm.anthropic import AnthropicLLM
from verifix.llm.base import LLM, LLMAuthError
from verifix.sandbox.docker import DockerSandbox


def build_llm(settings: Settings) -> LLM:
    if settings.anthropic_api_key is None:
        return _MissingKeyLLM()
    return AnthropicLLM(
        api_key=settings.anthropic_api_key.get_secret_value(),
        default_model=settings.model_main,
        timeout_s=settings.llm_timeout_s,
        max_attempts=settings.llm_max_attempts,
    )


def build_sandbox(settings: Settings) -> DockerSandbox:
    return DockerSandbox(
        base_image=settings.sandbox_base_image,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
        pids=settings.sandbox_pids,
        timeout_s=settings.sandbox_timeout_s,
        docker_bin=settings.docker_bin,
    )


def build_manager(settings: Settings) -> TaskManager:
    store = TaskStore(get_sessionmaker())
    llm = build_llm(settings)
    sandbox = build_sandbox(settings)
    return TaskManager(
        store, lambda: AgentLoop(store=store, llm=llm, sandbox=sandbox, settings=settings)
    )


class _MissingKeyLLM:
    """Stands in when no key is configured, so tasks fail with a clear message."""

    async def respond(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise LLMAuthError("API key missing or rejected")


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
    return app


app = create_app()

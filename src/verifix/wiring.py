"""Builds the real collaborators (model, sandbox, task runner) from settings."""

from verifix.agent.loop import AgentLoop
from verifix.agent.manager import TaskManager
from verifix.agent.store import TaskStore
from verifix.core.config import Settings
from verifix.core.db import get_sessionmaker
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

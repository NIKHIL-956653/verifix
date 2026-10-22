"""
Tool plumbing: typed inputs, a permission class, and a result the loop can record.

A tool is three things:
- `Input`: a Pydantic model. Its JSON Schema is what the model sees; validation runs
  before anything executes, so a malformed request never reaches a handler.
- `prepare(ctx, args)`: optional dry run. Validates against the current state (e.g. "the
  text you want to replace occurs exactly once") and produces the preview the user sees
  when approving. A request that fails here is returned to the model as an error
  without ever asking the user.
- `run(ctx, args)`: does the work and returns a ToolOutcome. If it produced something
  worth citing, it returns an EvidenceDraft and the loop stores it as evidence E<n>.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from verifix.agent.policy import Permission, Policy
from verifix.agent.store import TaskStore
from verifix.core.config import Settings
from verifix.llm.base import ToolSpec
from verifix.repos.paths import RepoJail
from verifix.sandbox.base import Sandbox

MAX_RESULT_CHARS = 12_000


@dataclass
class EvidenceDraft:
    kind: str
    summary: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutcome:
    content: str
    ok: bool = True
    evidence: EvidenceDraft | None = None
    finished: bool = False  # `finish` ends the run
    request_plan_approval: bool = False


@dataclass
class Prepared:
    preview: str = ""
    error: str | None = None
    state: Any = None  # whatever run() needs (e.g. the computed new file content)


@dataclass
class ToolContext:
    task_id: uuid.UUID
    kind: str
    request: str
    jail: RepoJail  # reads go here: the worktree, or the checkout for read-only tasks
    source_root: Path
    workspace_path: Path | None
    baseline: str | None
    source_head: str | None
    store: TaskStore
    sandbox: Sandbox
    settings: Settings
    policy: Policy
    env_image: str | None = None


Handler = Callable[[ToolContext, Any, Any], Awaitable[ToolOutcome]]
Preparer = Callable[[ToolContext, Any], Awaitable[Prepared]]


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: Permission
    run: Handler
    prepare: Preparer | None = None

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    @property
    def names(self) -> list[str]:
        return list(self._tools)


def clip(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated: {len(text) - limit} more characters]"

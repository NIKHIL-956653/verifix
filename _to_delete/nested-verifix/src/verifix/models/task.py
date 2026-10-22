"""
An engineering task: one user request against one repository.

Status vocabulary is deliberately explicit about what was and was not proven. A task
never reaches `verified` unless the verification engine recorded passing test evidence;
`fixed_unverified` exists so the UI can say "a patch exists but nothing has proven it".
`completed` is the end state of tasks that do not change code (questions, investigations,
reviews); their report says what was found and how well it is supported.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from verifix.models.base import Base

TASK_STATUSES = (
    "queued",
    "planning",
    "awaiting_approval",
    "running",
    "completed",
    "fixed_unverified",
    "verified",
    "failed",
    "cancelled",
    "budget_exhausted",
)
TERMINAL_STATUSES = frozenset(
    {"completed", "fixed_unverified", "verified", "failed", "cancelled", "budget_exhausted"}
)
TASK_KINDS = ("ask", "investigate", "fix", "review")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in TASK_STATUSES) + ")",
            name="status_valid",
        ),
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in TASK_KINDS) + ")",
            name="kind_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(Text, default="")
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str | None] = mapped_column(Text)

    # Who may do what without asking, and how much the task may spend.
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Agent state, persisted after every turn so a task can pause for approval and resume.
    conversation: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # The isolated copy the agent works in (M4). Never the user's checkout.
    worktree_path: Mapped[str | None] = mapped_column(Text)
    worktree_branch: Mapped[str | None] = mapped_column(String(200))
    baseline_commit: Mapped[str | None] = mapped_column(String(64))
    source_head: Mapped[str | None] = mapped_column(String(64))

    # Running totals, enforced against the budget by the agent loop.
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    active_seconds: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

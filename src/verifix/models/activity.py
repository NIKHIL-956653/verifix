"""
The execution timeline and the decisions around it.

- Step: one thing that happened in a task, in order - a model call, a tool call, an
  approval decision, a status change. This is what the UI timeline renders and what the
  benchmark mines for tool reliability and cost.
- Approval: a consequential action waiting for (or decided by) the user.
- Finding: one code-review result with its location and evidence.
- EvalRun: one benchmark run and its scored results.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from verifix.models.base import Base

STEP_KINDS = ("model_call", "tool_call", "approval", "status", "error")
APPROVAL_STATUSES = ("pending", "approved", "rejected")
SEVERITIES = ("critical", "high", "medium", "low", "info")
CONFIDENCE = ("confirmed", "plausible")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (
        CheckConstraint(_in("kind", STEP_KINDS), name="kind_valid"),
        UniqueConstraint("task_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(100))  # tool name, or model id
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence_seq: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 5), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (CheckConstraint(_in("status", APPROVAL_STATUSES), name="status_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(50))  # tool name or plan / export / ...
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    preview: Mapped[str] = mapped_column(Text, default="")  # e.g. the diff the edit would make
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(_in("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_in("confidence", CONFIDENCE), name="confidence_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(50))
    path: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[int]] = mapped_column(JSONB, default=list)
    confidence: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)  # why a claimed "confirmed" was downgraded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20))  # live | replay | record | scripted
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

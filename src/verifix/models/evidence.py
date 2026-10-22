"""
Evidence: the raw, unedited result of something verifix actually did.

A file read, a search, a git diff, a test run. The content is stored as produced (after
secret redaction) together with its SHA-256, so a report can cite evidence by id and
anyone can check the cited bytes were not altered afterwards. The final report is only
allowed to reference evidence rows that exist - that is the core "no fabrication" rule.

Rows are append-only by convention in M0; M3 adds the hash-chained audit trigger from eap.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from verifix.models.base import Base

EVIDENCE_KINDS = (
    "file_read",
    "file_list",
    "search",
    "symbols",
    "git_status",
    "git_diff",
    "edit",
    "test_run",
    "env_build",
    "plan",
    "note",
    "command",
)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in EVIDENCE_KINDS) + ")",
            name="kind_valid",
        ),
        CheckConstraint("char_length(content_sha256) = 64", name="sha256_len"),
        UniqueConstraint("task_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # order within the task
    kind: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text)  # one line, e.g. "read src/auth.py:40-88"
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    # Structured facts: path, line range, command, exit code, duration_ms...
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

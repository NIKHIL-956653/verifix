"""initial schema: repos, tasks, evidence

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_STATUSES = (
    "queued",
    "planning",
    "awaiting_approval",
    "running",
    "fixed_unverified",
    "verified",
    "failed",
    "cancelled",
    "budget_exhausted",
)
EVIDENCE_KINDS = ("file_read", "search", "git_status", "git_diff", "command", "test_run", "note")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # CHECK constraint names are short: the naming convention in verifix.models.base adds
    # the "ck_<table>_" prefix (giving e.g. ck_tasks_status_valid).
    op.create_table(
        "repos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_repos"),
        sa.UniqueConstraint("root_path", name="uq_repos_root_path"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name="fk_tasks_repo_id_repos", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("status", TASK_STATUSES), name="status_valid"),
    )
    op.create_index("ix_tasks_repo_id", "tasks", ["repo_id"])
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_evidence_task_id_tasks", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("kind", EVIDENCE_KINDS), name="kind_valid"),
        sa.CheckConstraint("char_length(content_sha256) = 64", name="sha256_len"),
        sa.UniqueConstraint("task_id", "seq", name="uq_evidence_task_id"),
    )
    op.create_index("ix_evidence_task_id", "evidence", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_task_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_tasks_repo_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("repos")

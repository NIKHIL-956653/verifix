"""
All database writes the agent makes, in one place.

Each method opens its own short session: the loop runs for minutes and must not hold a
transaction (or a pooled connection) across model calls. Sequence numbers are allocated
by the single writer of a task - the loop - so `max(seq)+1` is safe here.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from verifix.core.redact import redact, redact_text
from verifix.models import Approval, Evidence, Finding, Repo, Step, Task


def now() -> datetime:
    return datetime.now(UTC)


class TaskStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sessionmaker

    async def get(self, task_id: uuid.UUID) -> Task:
        async with self.sm() as s:
            task = await s.get(Task, task_id)
            if task is None:
                raise LookupError(f"task {task_id} not found")
            s.expunge(task)
            return task

    async def get_repo(self, repo_id: uuid.UUID) -> Repo:
        async with self.sm() as s:
            repo = await s.get(Repo, repo_id)
            if repo is None:
                raise LookupError(f"repo {repo_id} not found")
            s.expunge(repo)
            return repo

    async def update(self, task_id: uuid.UUID, **fields: Any) -> None:
        async with self.sm() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(**fields))
            await s.commit()

    async def add_usage(
        self,
        task_id: uuid.UUID,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
        cost: float,
    ) -> None:
        async with self.sm() as s:
            await s.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(
                    model_calls=Task.model_calls + 1,
                    input_tokens=Task.input_tokens + input_tokens,
                    output_tokens=Task.output_tokens + output_tokens,
                    cache_read_tokens=Task.cache_read_tokens + cache_read,
                    cache_write_tokens=Task.cache_write_tokens + cache_write,
                    cost_usd=Task.cost_usd + cost,
                )
            )
            await s.commit()

    async def add_tool_call(self, task_id: uuid.UUID) -> None:
        async with self.sm() as s:
            await s.execute(
                update(Task).where(Task.id == task_id).values(tool_calls=Task.tool_calls + 1)
            )
            await s.commit()

    async def cancel_requested(self, task_id: uuid.UUID) -> bool:
        async with self.sm() as s:
            return bool(
                (await s.execute(select(Task.cancel_requested).where(Task.id == task_id))).scalar()
            )

    # ------------------------------------------------------------------ evidence

    async def add_evidence(
        self, task_id: uuid.UUID, *, kind: str, summary: str, content: str, meta: dict[str, Any]
    ) -> int:
        content = redact_text(content)
        async with self.sm() as s:
            seq = (
                await s.execute(
                    select(func.coalesce(func.max(Evidence.seq), 0)).where(
                        Evidence.task_id == task_id
                    )
                )
            ).scalar_one() + 1
            s.add(
                Evidence(
                    task_id=task_id,
                    seq=seq,
                    kind=kind,
                    summary=redact_text(summary)[:500],
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    meta=redact(meta),
                )
            )
            await s.commit()
            return int(seq)

    async def evidence(self, task_id: uuid.UUID) -> list[Evidence]:
        async with self.sm() as s:
            rows = list(
                (
                    await s.execute(
                        select(Evidence).where(Evidence.task_id == task_id).order_by(Evidence.seq)
                    )
                ).scalars()
            )
            for r in rows:
                s.expunge(r)
            return rows

    # ------------------------------------------------------------------ timeline

    async def add_step(
        self,
        task_id: uuid.UUID,
        *,
        kind: str,
        name: str | None = None,
        input: dict[str, Any] | None = None,
        summary: str = "",
        ok: bool = True,
        evidence_seq: int | None = None,
        duration_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        async with self.sm() as s:
            seq = (
                await s.execute(
                    select(func.coalesce(func.max(Step.seq), 0)).where(Step.task_id == task_id)
                )
            ).scalar_one() + 1
            s.add(
                Step(
                    task_id=task_id,
                    seq=seq,
                    kind=kind,
                    name=name,
                    input=redact(input or {}),
                    summary=redact_text(summary)[:2000],
                    ok=ok,
                    evidence_seq=evidence_seq,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                )
            )
            await s.commit()
            return int(seq)

    # ------------------------------------------------------------------ approvals

    async def create_approval(
        self, task_id: uuid.UUID, *, action: str, payload: dict[str, Any], preview: str
    ) -> uuid.UUID:
        async with self.sm() as s:
            a = Approval(
                task_id=task_id,
                action=action,
                payload=redact(payload),
                preview=redact_text(preview),
            )
            s.add(a)
            await s.commit()
            return a.id

    async def get_approval(self, approval_id: uuid.UUID) -> Approval | None:
        async with self.sm() as s:
            a = await s.get(Approval, approval_id)
            if a is not None:
                s.expunge(a)
            return a

    async def decide_approval(
        self, approval_id: uuid.UUID, *, approve: bool, reason: str | None
    ) -> None:
        async with self.sm() as s:
            await s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(
                    status="approved" if approve else "rejected", reason=reason, decided_at=now()
                )
            )
            await s.commit()

    # ------------------------------------------------------------------ findings

    async def add_finding(self, task_id: uuid.UUID, **fields: Any) -> uuid.UUID:
        async with self.sm() as s:
            f = Finding(task_id=task_id, **fields)
            s.add(f)
            await s.commit()
            return f.id

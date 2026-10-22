"""
Tasks API: create, watch, approve, cancel, retry, and take the result.

The UI polls GET /tasks/{id} and /tasks/{id}/steps while a task runs.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from verifix.agent import workspace
from verifix.agent.manager import TaskManager
from verifix.agent.policy import Budget, Policy
from verifix.agent.store import now
from verifix.core.config import Settings, get_settings
from verifix.core.db import get_session
from verifix.models import (
    TASK_KINDS,
    TERMINAL_STATUSES,
    Approval,
    Evidence,
    Finding,
    Repo,
    Step,
    Task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
Session = Annotated[AsyncSession, Depends(get_session)]


def get_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise HTTPException(503, "task runner is not available (no model configured?)")
    return manager


Manager = Annotated[TaskManager, Depends(get_manager)]


class TaskIn(BaseModel):
    repo_id: uuid.UUID
    kind: Literal["ask", "investigate", "fix", "review"]
    request: str = Field(min_length=3, max_length=20_000)
    title: str | None = Field(None, max_length=200)
    policy: Policy | None = None
    budget: Budget | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    repo_id: uuid.UUID
    kind: str
    title: str
    request: str
    status: str
    error: str | None
    policy: dict[str, Any]
    budget: dict[str, Any]
    plan: dict[str, Any] | None
    report: dict[str, Any] | None
    worktree_path: str | None
    worktree_branch: str | None
    baseline_commit: str | None
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    active_seconds: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pending_approval: dict[str, Any] | None = None

    @classmethod
    def of(cls, t: Task, pending: Approval | None = None) -> "TaskOut":
        return cls(
            id=t.id,
            repo_id=t.repo_id,
            kind=t.kind,
            title=t.title,
            request=t.request,
            status=t.status,
            error=t.error,
            policy=t.policy or {},
            budget=t.budget or {},
            plan=t.plan,
            report=t.report,
            worktree_path=t.worktree_path,
            worktree_branch=t.worktree_branch,
            baseline_commit=t.baseline_commit,
            model_calls=t.model_calls,
            tool_calls=t.tool_calls,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            cache_read_tokens=t.cache_read_tokens,
            cost_usd=float(t.cost_usd or 0),
            active_seconds=float(t.active_seconds or 0),
            created_at=t.created_at,
            started_at=t.started_at,
            finished_at=t.finished_at,
            pending_approval=(
                {
                    "id": str(pending.id),
                    "action": pending.action,
                    "preview": pending.preview,
                    "payload": pending.payload,
                    "created_at": pending.created_at.isoformat() if pending.created_at else None,
                }
                if pending
                else None
            ),
        )


async def _task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


async def _pending(session: AsyncSession, task_id: uuid.UUID) -> Approval | None:
    return (
        (
            await session.execute(
                select(Approval)
                .where(Approval.task_id == task_id, Approval.status == "pending")
                .order_by(Approval.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


@router.post("", status_code=201, response_model=TaskOut)
async def create_task(
    body: TaskIn,
    session: Session,
    manager: Manager,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskOut:
    if body.kind not in TASK_KINDS:  # pragma: no cover - Literal already enforces
        raise HTTPException(422, "unknown kind")
    if await session.get(Repo, body.repo_id) is None:
        raise HTTPException(404, "repository not found")
    task = Task(
        repo_id=body.repo_id,
        kind=body.kind,
        title=(body.title or body.request.strip().splitlines()[0])[:200],
        request=body.request,
        status="queued",
        policy=(body.policy or Policy()).model_dump(),
        budget=(body.budget or Budget.from_settings(settings)).model_dump(),
        conversation=[],
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    manager.start(task.id)
    return TaskOut.of(task)


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    session: Session, repo_id: uuid.UUID | None = None, limit: int = 50
) -> list[TaskOut]:
    q = select(Task).order_by(Task.created_at.desc()).limit(min(limit, 200))
    if repo_id:
        q = q.where(Task.repo_id == repo_id)
    return [TaskOut.of(t) for t in (await session.execute(q)).scalars()]


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, session: Session) -> TaskOut:
    t = await _task(session, task_id)
    return TaskOut.of(t, await _pending(session, task_id))


@router.get("/{task_id}/steps")
async def get_steps(task_id: uuid.UUID, session: Session, after: int = 0) -> list[dict[str, Any]]:
    await _task(session, task_id)
    rows = (
        await session.execute(
            select(Step).where(Step.task_id == task_id, Step.seq > after).order_by(Step.seq)
        )
    ).scalars()
    return [
        {
            "seq": s.seq,
            "kind": s.kind,
            "name": s.name,
            "input": s.input,
            "summary": s.summary,
            "ok": s.ok,
            "evidence": s.evidence_seq,
            "duration_ms": s.duration_ms,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "cost_usd": float(s.cost_usd or 0),
            "at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in rows
    ]


@router.get("/{task_id}/evidence")
async def list_evidence(task_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    await _task(session, task_id)
    rows = (
        await session.execute(
            select(Evidence).where(Evidence.task_id == task_id).order_by(Evidence.seq)
        )
    ).scalars()
    return [
        {
            "seq": e.seq,
            "kind": e.kind,
            "summary": e.summary,
            "meta": e.meta,
            "sha256": e.content_sha256,
        }
        for e in rows
    ]


@router.get("/{task_id}/evidence/{seq}")
async def get_evidence(task_id: uuid.UUID, seq: int, session: Session) -> dict[str, Any]:
    e = (
        await session.execute(
            select(Evidence).where(Evidence.task_id == task_id, Evidence.seq == seq)
        )
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, "evidence not found")
    return {
        "seq": e.seq,
        "kind": e.kind,
        "summary": e.summary,
        "meta": e.meta,
        "content": e.content,
        "sha256": e.content_sha256,
    }


@router.get("/{task_id}/findings")
async def get_findings(task_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    await _task(session, task_id)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows = list(
        (await session.execute(select(Finding).where(Finding.task_id == task_id))).scalars()
    )
    rows.sort(key=lambda f: (order.get(f.severity, 9), f.path, f.line_start))
    return [
        {
            "id": str(f.id),
            "severity": f.severity,
            "category": f.category,
            "path": f.path,
            "line_start": f.line_start,
            "line_end": f.line_end,
            "title": f.title,
            "explanation": f.explanation,
            "recommendation": f.recommendation,
            "evidence": f.evidence,
            "confidence": f.confidence,
            "note": f.note,
        }
        for f in rows
    ]


@router.get("/{task_id}/diff")
async def get_diff(task_id: uuid.UUID, session: Session) -> dict[str, Any]:
    t = await _task(session, task_id)
    if not t.worktree_path or not t.baseline_commit:
        return {"diff": "", "files": [], "stats": {"files": 0, "added": 0, "removed": 0}}
    from pathlib import Path

    p = Path(t.worktree_path)
    if not await asyncio.to_thread(p.is_dir):
        raise HTTPException(410, "the workspace for this task has been removed")
    diff, files, stats = await asyncio.gather(
        asyncio.to_thread(workspace.diff, p, t.baseline_commit),
        asyncio.to_thread(workspace.changed_files, p, t.baseline_commit),
        asyncio.to_thread(workspace.diff_stats, p, t.baseline_commit),
    )
    return {"diff": diff, "files": files, "stats": stats}


@router.get("/{task_id}/patch")
async def download_patch(task_id: uuid.UUID, session: Session) -> Response:
    t = await _task(session, task_id)
    if not t.worktree_path or not t.baseline_commit:
        raise HTTPException(404, "this task has no changes")
    from pathlib import Path

    text = await asyncio.to_thread(
        workspace.diff, Path(t.worktree_path), t.baseline_commit, binary=True
    )
    return Response(
        text,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="verifix-{str(task_id)[:8]}.patch"'},
    )


class DecisionIn(BaseModel):
    approve: bool
    reason: str | None = Field(None, max_length=2000)


@router.post("/{task_id}/approvals/{approval_id}", response_model=TaskOut)
async def decide(
    task_id: uuid.UUID, approval_id: uuid.UUID, body: DecisionIn, session: Session, manager: Manager
) -> TaskOut:
    t = await _task(session, task_id)
    a = await session.get(Approval, approval_id)
    if a is None or a.task_id != task_id:
        raise HTTPException(404, "approval not found")
    if a.status != "pending":
        raise HTTPException(409, f"already {a.status}")
    if t.status != "awaiting_approval":
        raise HTTPException(409, f"task is {t.status}, not awaiting approval")
    a.status = "approved" if body.approve else "rejected"
    a.reason = body.reason
    a.decided_at = now()
    await session.commit()
    manager.start(task_id)
    await session.refresh(t)
    return TaskOut.of(t)


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel(task_id: uuid.UUID, session: Session, manager: Manager) -> TaskOut:
    t = await _task(session, task_id)
    if t.status in TERMINAL_STATUSES:
        raise HTTPException(409, f"task already {t.status}")
    await manager.cancel(task_id)
    await session.refresh(t)
    return TaskOut.of(t)


class RetryIn(BaseModel):
    budget: Budget | None = Field(None, description="Optionally raise the budget before continuing")


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry(
    task_id: uuid.UUID, session: Session, manager: Manager, body: RetryIn | None = None
) -> TaskOut:
    """Continue a failed or budget-exhausted task from its saved conversation."""
    t = await _task(session, task_id)
    if t.status not in ("failed", "budget_exhausted"):
        raise HTTPException(
            409, f"only failed or budget-exhausted tasks can be retried (task is {t.status})"
        )
    if body and body.budget:
        t.budget = body.budget.model_dump()
    t.status = "running"
    t.error = None
    t.finished_at = None
    t.report = None
    t.cancel_requested = False
    await session.commit()
    manager.start(task_id)
    await session.refresh(t)
    return TaskOut.of(t)


class ExportIn(BaseModel):
    confirm: bool = Field(description="Must be true: this writes to your working tree")


@router.post("/{task_id}/apply")
async def apply_to_checkout(task_id: uuid.UUID, body: ExportIn, session: Session) -> dict[str, Any]:
    """Apply the agent's change to the user's checkout. Explicit, logged, conflict-checked."""
    if not body.confirm:
        raise HTTPException(422, "confirm must be true")
    t = await _task(session, task_id)
    if t.status not in ("verified", "fixed_unverified", "completed"):
        raise HTTPException(409, f"task is {t.status}; wait for it to finish")
    if not t.worktree_path or not t.baseline_commit:
        raise HTTPException(404, "this task has no changes")
    repo = await session.get(Repo, t.repo_id)
    assert repo is not None  # noqa: S101 - FK
    from pathlib import Path

    approval = Approval(
        task_id=t.id,
        action="apply_to_checkout",
        payload={},
        preview="",
        status="approved",
        decided_at=now(),
    )
    session.add(approval)
    res = await asyncio.to_thread(
        workspace.apply_to_source, Path(t.worktree_path), t.baseline_commit, Path(repo.root_path)
    )
    approval.reason = res.message
    session.add(
        Step(
            task_id=t.id,
            seq=await _next_step_seq(session, t.id),
            kind="approval",
            name="apply_to_checkout",
            summary=res.message,
            ok=res.applied,
        )
    )
    await session.commit()
    return {
        "applied": res.applied,
        "files": res.files,
        "conflicts": res.conflicts,
        "message": res.message,
    }


async def _next_step_seq(session: AsyncSession, task_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return (
        int(
            (
                await session.execute(
                    select(func.coalesce(func.max(Step.seq), 0)).where(Step.task_id == task_id)
                )
            ).scalar_one()
        )
        + 1
    )


@router.delete("/{task_id}/workspace", status_code=204)
async def delete_workspace(task_id: uuid.UUID, session: Session, manager: Manager) -> None:
    t = await _task(session, task_id)
    if manager.is_running(task_id):
        raise HTTPException(409, "task is running")
    if t.worktree_path:
        repo = await session.get(Repo, t.repo_id)
        from pathlib import Path

        await asyncio.to_thread(
            workspace.remove,
            Path(repo.root_path) if repo else Path(t.worktree_path),
            Path(t.worktree_path),
            t.worktree_branch,
        )
        t.worktree_path = None
        await session.commit()

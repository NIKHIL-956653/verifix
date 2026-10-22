"""
Benchmark API (M7): list cases, start runs, read scored results for the dashboard.

Runs execute in the background of the API process. A run with the real agent spends
Anthropic credit, so it requires `confirm_cost: true` - the API's approval for that action.
Replay of recorded cassettes is a CLI feature (`python -m verifix eval --mode replay`).
"""

import asyncio
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from verifix.core.config import Settings, get_settings
from verifix.core.db import get_session, get_sessionmaker
from verifix.core.logging import get_logger
from verifix.evals.cases import load_cases
from verifix.evals.grader import Grader
from verifix.evals.runner import (
    AgentSolver,
    BenchmarkReport,
    CaseResult,
    NoopSolver,
    ReferenceSolver,
    Solver,
    run_benchmark,
    save_run,
    summarise,
)
from verifix.models import EvalRun

router = APIRouter(prefix="/evals", tags=["evals"])
Session = Annotated[AsyncSession, Depends(get_session)]
log = get_logger("verifix.evals")


class EvalIn(BaseModel):
    solver: Literal["reference", "noop", "agent"]
    cases: list[str] | None = None
    name: str | None = Field(None, max_length=200)
    confirm_cost: bool = False
    max_cost_usd_per_case: float = Field(1.0, gt=0, le=10)


class EvalRunOut(BaseModel):
    id: uuid.UUID
    name: str
    mode: str
    config: dict[str, Any]
    summary: dict[str, Any]
    created_at: datetime
    results: list[Any] | None = None


def _out(r: EvalRun, *, with_results: bool) -> EvalRunOut:
    return EvalRunOut(
        id=r.id,
        name=r.name,
        mode=r.mode,
        config=r.config or {},
        summary=r.summary or {},
        created_at=r.created_at,
        results=(r.results or []) if with_results else None,
    )


@router.get("/cases")
async def list_cases() -> list[dict[str, Any]]:
    return [c.public() for c in load_cases()]


@router.get("", response_model=list[EvalRunOut])
async def list_runs(session: Session) -> list[EvalRunOut]:
    rows = (
        (await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(100)))
        .scalars()
        .all()
    )
    return [_out(r, with_results=False) for r in rows]


@router.get("/{run_id}", response_model=EvalRunOut)
async def get_run(run_id: uuid.UUID, session: Session) -> EvalRunOut:
    row = await session.get(EvalRun, run_id)
    if row is None:
        raise HTTPException(404, "eval run not found")
    return _out(row, with_results=True)


def _sandbox(request: Request, settings: Settings):  # type: ignore[no-untyped-def]
    sb = getattr(request.app.state, "eval_sandbox", None)
    if sb is None:
        from verifix.wiring import build_sandbox

        sb = build_sandbox(settings)
    return sb


def _llm(request: Request, settings: Settings):  # type: ignore[no-untyped-def]
    llm = getattr(request.app.state, "eval_llm", None)
    if llm is None:
        from verifix.wiring import build_llm

        llm = build_llm(settings)
    return llm


@router.post("", status_code=202, response_model=EvalRunOut)
async def start_run(
    body: EvalIn,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvalRunOut:
    try:
        cases = load_cases(body.cases)
    except KeyError as e:
        raise HTTPException(422, str(e)) from None
    if body.solver == "agent" and not body.confirm_cost:
        raise HTTPException(
            422,
            f"running the agent on {len(cases)} case(s) spends Anthropic credit (up to "
            f"${body.max_cost_usd_per_case:.2f} each); resend with confirm_cost=true",
        )
    sandbox = _sandbox(request, settings)
    ok, why = await asyncio.to_thread(sandbox.available)
    if not ok:
        raise HTTPException(503, f"sandbox unavailable: {why}")

    mode = "live" if body.solver == "agent" else "scripted"
    row = EvalRun(
        name=body.name or f"{body.solver}-{datetime.now():%Y%m%d-%H%M%S}",
        mode=mode,
        config={"solver": body.solver, "cases": [c.id for c in cases], "mode": mode},
        summary={"state": "running", "cases": len(cases), "done": 0},
        results=[],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    sm = getattr(request.app.state, "eval_sessionmaker", None) or get_sessionmaker()
    scratch = Path(tempfile.mkdtemp(prefix="verifix-bench-"))
    solver: Solver
    if body.solver == "reference":
        solver = ReferenceSolver(scratch / "ref")
    elif body.solver == "noop":
        solver = NoopSolver()
    else:
        from verifix.agent.policy import Budget

        solver = AgentSolver(
            sessionmaker=sm,
            settings=settings,
            sandbox=sandbox,
            # A fresh client per case (unless a test injected one): its retry budget and
            # circuit breaker then cannot carry one case's outage into the next.
            llm_for=lambda _case: _llm(request, settings),
            budget=Budget(
                max_model_calls=30, max_cost_usd=body.max_cost_usd_per_case, max_minutes=10
            ),
        )
    run_id = row.id
    done: list[CaseResult] = []

    async def progress() -> None:
        async with sm() as s:
            r = await s.get(EvalRun, run_id)
            if r is not None:
                r.summary = {
                    **summarise(done),
                    "state": "running",
                    "cases": len(cases),
                    "done": len(done),
                }
                await s.commit()

    pending_writes: set[asyncio.Task[None]] = set()

    def on_result(r: CaseResult) -> None:
        done.append(r)
        t = asyncio.create_task(progress())
        pending_writes.add(t)
        t.add_done_callback(pending_writes.discard)

    async def run() -> None:
        try:
            report: BenchmarkReport = await run_benchmark(
                solver,
                cases,
                Grader(sandbox, scratch / "grader"),
                scratch / "work",
                on_result=on_result,
            )
            if pending_writes:
                await asyncio.gather(*pending_writes, return_exceptions=True)
            await save_run(
                sm, name=row.name, mode=mode, config=row.config, report=report, run_id=run_id
            )
        except Exception as e:
            log.exception("eval run failed", run_id=str(run_id))
            async with sm() as s:
                r = await s.get(EvalRun, run_id)
                if r is not None:
                    r.summary = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:500]}
                    await s.commit()

    runs: dict[uuid.UUID, asyncio.Task[None]] | None = getattr(request.app.state, "eval_runs", None)
    if runs is None:
        runs = {}
        request.app.state.eval_runs = runs
    runs[run_id] = asyncio.create_task(run(), name=f"verifix-eval-{run_id}")
    return _out(row, with_results=True)

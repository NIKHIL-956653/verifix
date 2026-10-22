"""
Solvers, the benchmark loop, metrics, and ruler validation.

A solver turns a case into a patch. Three exist:

- `reference`: the case's reference fix. Must score 100% - validates the grader.
- `noop`:      an empty patch. Must score 0% - proves the hidden tests discriminate.
- `agent`:     the real verifix agent loop, fully autonomous (Policy.autonomous()), with
               the live model, a recording of it, or a replay of a recording.

Only the agent solver produces agent-side metrics (status, evidence coverage, tool
reliability, cost). Everything about correctness comes from the grader.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from verifix.agent import workspace
from verifix.agent.loop import AgentLoop
from verifix.agent.policy import Budget, Policy
from verifix.agent.store import TaskStore
from verifix.core.config import Settings
from verifix.evals.cases import Case, copy_tree, materialise, reference_patch
from verifix.evals.grader import Grade, Grader, open_permissions
from verifix.llm.base import LLM
from verifix.models import EvalRun, Repo, Step, Task
from verifix.sandbox.base import Sandbox


@dataclass
class SolveResult:
    patch: str
    agent_status: str | None = None
    evidence_coverage: float | None = None
    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    task_id: str | None = None
    error: str | None = None


class Solver(Protocol):
    name: str

    async def solve(self, case: Case, repo: Path) -> SolveResult: ...


class ReferenceSolver:
    name = "reference"

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch

    async def solve(self, case: Case, repo: Path) -> SolveResult:
        t0 = time.monotonic()
        patch = await asyncio.to_thread(reference_patch, case, self.scratch)
        return SolveResult(patch=patch, duration_s=time.monotonic() - t0)


class NoopSolver:
    name = "noop"

    async def solve(self, case: Case, repo: Path) -> SolveResult:
        return SolveResult(patch="")


class AgentSolver:
    """Runs the real agent on the case repo, autonomously, and returns its workspace diff."""

    name = "agent"

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        sandbox: Sandbox,
        llm_for: Callable[[Case], LLM],
        budget: Budget | None = None,
        keep_workspaces: bool = False,
    ) -> None:
        self.sm = sessionmaker
        self.settings = settings
        self.sandbox = sandbox
        self.llm_for = llm_for
        self.budget = budget or Budget(max_model_calls=30, max_cost_usd=1.0, max_minutes=10)
        self.keep_workspaces = keep_workspaces

    async def solve(self, case: Case, repo: Path) -> SolveResult:
        store = TaskStore(self.sm)
        async with self.sm() as s:
            row = Repo(name=f"bench-{case.id}", root_path=str(repo), default_branch="main")
            s.add(row)
            await s.flush()
            task = Task(
                repo_id=row.id,
                kind="fix",
                title=f"[bench] {case.title}"[:200],
                request=case.request,
                status="queued",
                policy=Policy.autonomous().model_dump(),
                budget=self.budget.model_dump(),
                conversation=[],
            )
            s.add(task)
            await s.commit()
            task_id = task.id
        t0 = time.monotonic()
        loop = AgentLoop(
            store=store, llm=self.llm_for(case), sandbox=self.sandbox, settings=self.settings
        )
        await loop.run(task_id)
        done = await store.get(task_id)
        patch = ""
        if done.worktree_path and done.baseline_commit:
            ws = Path(done.worktree_path)
            if await asyncio.to_thread(ws.is_dir):
                patch = await asyncio.to_thread(workspace.diff, ws, done.baseline_commit)
                if not self.keep_workspaces:
                    await asyncio.to_thread(workspace.remove, repo, ws, done.worktree_branch)
        async with self.sm() as s:
            errors = (
                await s.execute(
                    select(func.count())
                    .select_from(Step)
                    .where(Step.task_id == task_id, Step.kind == "tool_call", Step.ok.is_(False))
                )
            ).scalar_one()
        report = done.report or {}
        return SolveResult(
            patch=patch,
            agent_status=done.status,
            evidence_coverage=report.get("evidence_coverage"),
            model_calls=done.model_calls,
            tool_calls=done.tool_calls,
            tool_errors=int(errors),
            input_tokens=done.input_tokens,
            output_tokens=done.output_tokens,
            cost_usd=float(done.cost_usd or 0),
            duration_s=time.monotonic() - t0,
            task_id=str(task_id),
            error=done.error if done.status == "failed" else None,
        )


# ---------------------------------------------------------------------- running


@dataclass
class CaseResult:
    case_id: str
    category: str
    grade: dict[str, Any]
    solve: dict[str, Any]
    false_verified: bool = False

    @property
    def success(self) -> bool:
        return bool(self.grade.get("success"))


@dataclass
class BenchmarkReport:
    solver: str
    summary: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"cases": 0}
    precisions = [
        r.grade["patch_precision"] for r in results if r.grade.get("patch_precision") is not None
    ]
    coverages = [
        r.solve["evidence_coverage"]
        for r in results
        if r.solve.get("evidence_coverage") is not None
    ]
    tool_calls = sum(r.solve.get("tool_calls", 0) for r in results)
    tool_errors = sum(r.solve.get("tool_errors", 0) for r in results)
    claimed = [r for r in results if r.solve.get("agent_status") == "verified"]
    return {
        "cases": n,
        "successes": sum(r.success for r in results),
        "success_rate": round(sum(r.success for r in results) / n, 3),
        "patch_applies_rate": round(sum(bool(r.grade["patch_applies"]) for r in results) / n, 3),
        "cases_with_regressions": sum(bool(r.grade["regressions"]) for r in results),
        "mean_patch_precision": round(mean(precisions), 3) if precisions else None,
        "mean_evidence_coverage": round(mean(coverages), 3) if coverages else None,
        "tool_reliability": round(1 - tool_errors / tool_calls, 3) if tool_calls else None,
        "claimed_verified": len(claimed),
        "false_verified": sum(r.false_verified for r in results),
        "verified_precision": (
            round(sum(r.success for r in claimed) / len(claimed), 3) if claimed else None
        ),
        "total_cost_usd": round(sum(r.solve.get("cost_usd", 0.0) for r in results), 4),
        "mean_duration_s": round(mean(r.solve.get("duration_s", 0.0) for r in results), 2),
        "by_category": {
            cat: sum(r.success for r in results if r.category == cat)
            for cat in sorted({r.category for r in results})
        },
    }


async def run_benchmark(
    solver: Solver,
    cases: list[Case],
    grader: Grader,
    work_dir: Path,
    *,
    on_result: Callable[[CaseResult], Any] | None = None,
) -> BenchmarkReport:
    results: list[CaseResult] = []
    for case in cases:
        repo = await asyncio.to_thread(materialise, case, work_dir / case.id)
        await asyncio.to_thread(open_permissions, repo)
        try:
            solved = await solver.solve(case, repo)
        except Exception as e:  # a crashing solver is a failed case, not a crashed benchmark
            solved = SolveResult(patch="", error=f"{type(e).__name__}: {e}"[:500])
        grade: Grade = await grader.grade(case, solved.patch)
        result = CaseResult(
            case_id=case.id,
            category=case.category,
            grade=grade.to_dict(),
            solve=asdict(solved),
            false_verified=solved.agent_status == "verified" and not grade.success,
        )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return BenchmarkReport(
        solver.name, summarise(results), [asdict(r) | {"success": r.success} for r in results]
    )


async def validate_ruler(cases: list[Case], grader: Grader, scratch: Path) -> dict[str, Any]:
    """The benchmark is only meaningful if reference = 100% and noop = 0%."""
    ref = await run_benchmark(ReferenceSolver(scratch), cases, grader, scratch / "ruler-ref")
    noop = await run_benchmark(NoopSolver(), cases, grader, scratch / "ruler-noop")
    discriminating: dict[str, bool] = {}
    for case in cases:  # hidden tests must FAIL on the buggy code, else they test nothing
        tree = await asyncio.to_thread(
            materialise, case, scratch / f"discriminate-{case.id}-{uuid.uuid4().hex[:6]}"
        )
        copy_tree(case.hidden_dir, tree)
        open_permissions(tree)
        res = await grader.run_tests(tree, case.hidden_files())
        discriminating[case.id] = res.status == "failed"
    ok = (
        ref.summary["success_rate"] == 1.0
        and noop.summary["success_rate"] == 0.0
        and all(discriminating.values())
    )
    return {
        "ok": ok,
        "reference_success_rate": ref.summary["success_rate"],
        "noop_success_rate": noop.summary["success_rate"],
        "hidden_tests_fail_on_bug": discriminating,
        "reference_failures": [r["case_id"] for r in ref.results if not r["success"]],
    }


async def save_run(
    sm: async_sessionmaker[AsyncSession],
    *,
    name: str,
    mode: str,
    config: dict[str, Any],
    report: BenchmarkReport,
    run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with sm() as s:
        row = await s.get(EvalRun, run_id) if run_id else None
        if row is None:
            row = EvalRun(id=run_id or uuid.uuid4(), name=name, mode=mode, config=config)
            s.add(row)
        row.summary = {**report.summary, "state": "finished", "solver": report.solver}
        row.results = report.results
        await s.commit()
        return row.id

"""Planning, review findings, and the final report."""

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, Field

from verifix.agent import workspace
from verifix.agent.report import ClaimIn, EvidenceRef, FinishData, reconcile
from verifix.agent.store import now
from verifix.repos import files
from verifix.repos.paths import PathRejected
from verifix.tools.base import EvidenceDraft, Tool, ToolContext, ToolOutcome

# ------------------------------------------------------------------ plan


class Hypothesis(BaseModel):
    statement: str
    how_to_check: str


class PlanIn(BaseModel):
    objective: str
    relevant_files: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    steps: list[str]
    acceptance_criteria: list[str]
    required_tests: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    verification_plan: str


async def submit_plan(ctx: ToolContext, a: PlanIn, _p: object) -> ToolOutcome:
    plan = a.model_dump()
    await ctx.store.update(ctx.task_id, plan=plan)
    needs_ok = ctx.kind == "fix" and ctx.policy.approve_plan
    msg = "plan recorded." + (
        " The user must approve it before you change any file; wait for their decision."
        if needs_ok
        else ""
    )
    return ToolOutcome(
        msg,
        evidence=EvidenceDraft(
            "plan", f"plan: {a.objective}"[:200], json.dumps(plan, indent=1), {}
        ),
        request_plan_approval=needs_ok,
    )


# ------------------------------------------------------------------ review findings


class FindingIn(BaseModel):
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: Literal[
        "logic",
        "validation",
        "error_handling",
        "security",
        "performance",
        "concurrency",
        "database",
        "auth",
        "compatibility",
        "tests",
        "other",
    ]
    path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    title: str
    explanation: str
    recommendation: str
    evidence: list[int] = Field(
        description="Evidence numbers that show the problem, e.g. [4] for E4"
    )
    confidence: Literal["confirmed", "plausible"] = Field(
        description=(
            "'confirmed' only if the cited evidence demonstrates the defect (e.g. a failing "
            "test); otherwise 'plausible'"
        )
    )


async def report_finding(ctx: ToolContext, a: FindingIn, _p: object) -> ToolOutcome:
    if a.line_end < a.line_start:
        return ToolOutcome("line_end must be >= line_start", ok=False)
    try:
        r = await asyncio.to_thread(files.read_file, ctx.jail, a.path, a.line_start, a.line_start)
    except (PathRejected, files.NotReadable) as e:
        return ToolOutcome(f"invalid location {a.path}: {getattr(e, 'reason', e)}", ok=False)
    if a.line_end > r.total_lines:
        return ToolOutcome(f"{a.path} has only {r.total_lines} lines", ok=False)

    evidence = {e.seq: e for e in await ctx.store.evidence(ctx.task_id)}
    missing = [i for i in a.evidence if i not in evidence]
    if missing:
        return ToolOutcome(f"evidence {missing} does not exist in this task", ok=False)

    # Rule: reading the code shows the lines exist; only a failing test demonstrates the
    # defect. "confirmed" therefore requires a cited test run that failed.
    confidence = a.confidence
    note = None
    if confidence == "confirmed" and not any(
        evidence[i].kind == "test_run" and (evidence[i].meta or {}).get("status") == "failed"
        for i in a.evidence
    ):
        confidence = "plausible"
        note = "downgraded to plausible: no failing test run among the cited evidence"
    if not any(
        evidence[i].kind in ("file_read", "test_run", "git_diff", "search") for i in a.evidence
    ):
        return ToolOutcome(
            "a finding must cite evidence that shows the code (a file read, diff, search or test "
            "run)",
            ok=False,
        )
    fid = await ctx.store.add_finding(
        ctx.task_id,
        severity=a.severity,
        category=a.category,
        path=r.path,
        line_start=a.line_start,
        line_end=a.line_end,
        title=a.title,
        explanation=a.explanation,
        recommendation=a.recommendation,
        evidence=a.evidence,
        confidence=confidence,
        note=note,
    )
    return ToolOutcome(
        f"finding recorded ({a.severity}, {confidence}) at {r.path}:{a.line_start}-{a.line_end}"
        + (f"; {note}" if note else "")
        + f" [id {str(fid)[:8]}]"
    )


# ------------------------------------------------------------------ finish


class Claim(BaseModel):
    statement: str
    evidence: list[int] = Field(
        description="Evidence numbers supporting this statement, e.g. [3, 7] for E3 and E7"
    )


class FinishIn(BaseModel):
    outcome: Literal[
        "answered",
        "root_cause_suspected",
        "reproduced",
        "fixed",
        "no_fix_needed",
        "review_complete",
        "blocked",
    ]
    summary: str = Field(description="What you found or did, in a few sentences")
    root_cause: str | None = None
    claims: list[Claim] = Field(
        description="Each important conclusion with the evidence that supports it"
    )
    uncertainty: str = Field(description="What is still unproven or assumed")
    next_steps: list[str] = Field(default_factory=list)


async def finish(ctx: ToolContext, a: FinishIn, _p: object) -> ToolOutcome:
    rows = await ctx.store.evidence(ctx.task_id)
    refs = [EvidenceRef(r.seq, r.kind, r.meta or {}) for r in rows]
    changed: list[str] = []
    if ctx.workspace_path and ctx.baseline:
        changed = await asyncio.to_thread(workspace.changed_files, ctx.workspace_path, ctx.baseline)
    status, report = reconcile(
        ctx.kind,
        FinishData(
            a.outcome,
            a.summary,
            a.root_cause,
            [ClaimIn(c.statement, c.evidence) for c in a.claims],
            a.uncertainty,
            a.next_steps,
        ),
        refs,
        changed,
    )
    if ctx.workspace_path and ctx.baseline:
        report["diff_stats"] = await asyncio.to_thread(
            workspace.diff_stats, ctx.workspace_path, ctx.baseline
        )
    await ctx.store.update(
        ctx.task_id, report=report, status=status, finished_at=now(), pending=None
    )
    return ToolOutcome(f"task finished with status {status}", finished=True)


PLAN_TOOL = Tool(
    "submit_plan",
    "Record your structured plan: objective, relevant files, hypotheses, steps, acceptance "
    "criteria, tests and risks.",
    PlanIn,
    "report",
    submit_plan,
)
FINDING_TOOL = Tool(
    "report_finding",
    "Record one code-review finding with its exact location and the evidence for it.",
    FindingIn,
    "report",
    report_finding,
)
FINISH_TOOL = Tool(
    "finish",
    "End the task with your report. Cite evidence numbers for every claim. The system checks your "
    "outcome against the recorded evidence.",
    FinishIn,
    "report",
    finish,
)

"""Running tests - only inside the sandbox, only pytest, only validated arguments (ADR-007)."""

import uuid

from pydantic import BaseModel, Field

from verifix.sandbox.base import InvalidTestArgs, validate_keyword, validate_target
from verifix.tools.base import EvidenceDraft, Prepared, Tool, ToolContext, ToolOutcome, clip


class PrepareEnvIn(BaseModel):
    pass


async def prepare_environment(ctx: ToolContext, _a: PrepareEnvIn, _p: object) -> ToolOutcome:
    if ctx.workspace_path is None:
        return ToolOutcome("this task has no workspace to build an environment for", ok=False)
    env = await ctx.sandbox.ensure_environment(ctx.workspace_path, allow_install=True)
    if env.ok:
        ctx.env_image = env.image
    text = (
        f"environment {'ready' if env.ok else 'NOT ready'}: image={env.image} "
        f"built={env.built}"
        + (f"\n{env.limitation}" if env.limitation else "")
        + (f"\n{env.log_tail}" if env.log_tail else "")
    )
    return ToolOutcome(
        clip(text),
        ok=env.ok,
        evidence=EvidenceDraft(
            "env_build", f"prepare environment: {'ok' if env.ok else 'failed'}", text, env.to_dict()
        ),
    )


class RunTestsIn(BaseModel):
    targets: list[str] = Field(
        default_factory=list,
        description=(
            "Test files or node ids, e.g. ['tests/test_auth.py::test_missing_email']. Empty = the "
            "whole suite."
        ),
        max_length=20,
    )
    keyword: str | None = Field(None, description="Optional pytest -k expression")
    purpose: str = Field(
        description="What this run is meant to show, e.g. 'reproduce the bug before fixing'"
    )


async def prepare_run(ctx: ToolContext, a: RunTestsIn) -> Prepared:
    if ctx.workspace_path is None:
        return Prepared(error="this task has no workspace; tests cannot run")
    try:
        targets = [validate_target(t) for t in a.targets]
        if a.keyword:
            validate_keyword(a.keyword)
    except InvalidTestArgs as e:
        return Prepared(error=str(e))
    for t in targets:
        path = t.split("::", 1)[0]
        if not ctx.jail.is_allowed(path) or not (ctx.jail.root / path).exists():
            return Prepared(error=f"test path not found in the workspace: {path}")
    shown = (
        "pytest"
        + (f" -k {a.keyword!r}" if a.keyword else "")
        + (" " + " ".join(targets) if targets else "")
    )
    return Prepared(preview=f"{shown}  (sandbox: no network, read-only)", state=targets)


async def run_tests(ctx: ToolContext, a: RunTestsIn, _p: object) -> ToolOutcome:
    p = await prepare_run(ctx, a)
    if p.error:
        return ToolOutcome(p.error, ok=False)
    assert ctx.workspace_path is not None  # noqa: S101 - checked in prepare
    res = await ctx.sandbox.run_pytest(
        ctx.workspace_path,
        targets=p.state,
        keyword=a.keyword,
        image=ctx.env_image,
        run_id=uuid.uuid4().hex[:10],
    )
    lines = [res.summary_line()]
    if res.failing_tests:
        lines.append("failing: " + ", ".join(res.failing_tests[:20]))
    if res.limitation:
        lines.append(f"limitation: {res.limitation}")
    if res.output_tail:
        lines.append("--- output (tail) ---\n" + res.output_tail[-6000:])
    text = "\n".join(lines)
    meta = res.to_dict()
    meta.pop("output_tail", None)
    meta.update({"targets": p.state, "keyword": a.keyword, "purpose": a.purpose})
    # A tool call that ran correctly is ok even when tests fail: failing tests are a result.
    return ToolOutcome(
        clip(text),
        ok=res.status not in ("blocked",),
        evidence=EvidenceDraft("test_run", f"{res.summary_line()} — {a.purpose}"[:300], text, meta),
    )


TEST_TOOLS = [
    Tool(
        "prepare_environment",
        "Install the repository's declared dependencies into a sandbox image. Needs network, so "
        "it requires approval unless the policy allows installs.",
        PrepareEnvIn,
        "install",
        prepare_environment,
    ),
    Tool(
        "run_tests",
        "Run pytest in the sandbox (no network, read-only filesystem, resource limits) on your "
        "workspace. The result comes from the real exit code.",
        RunTestsIn,
        "execute",
        run_tests,
        prepare_run,
    ),
]

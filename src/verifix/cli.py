"""
Command line: `python -m verifix <command>`.

    setup-sandbox                 build the sandbox runtime image (base + pytest), once
    cases                         list benchmark cases
    eval --solver reference       validate the ruler (must be 100%)
    eval --solver noop            validate the ruler (must be 0%)
    eval --solver agent --mode replay --cassettes evals/cassettes/run1
    eval --solver agent --mode record --cassettes evals/cassettes/run1 --confirm-cost
    eval --validate-ruler         reference + noop + "hidden tests fail on the bug"

Live and record modes spend Anthropic credit, so they refuse to start without
--confirm-cost (the CLI equivalent of an approval).
"""

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from verifix.core.config import get_settings


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def _setup_sandbox() -> int:
    from verifix.wiring import build_sandbox

    sb = build_sandbox(get_settings())
    ok, why = sb.available()
    if not ok:
        print(f"Docker is not available: {why}")
        return 1
    print(f"building {sb.runtime_tag} from {sb.base_image} (pulls the base image if needed)...")
    env = await sb.ensure_runtime(allow_install=True)
    print(("ready: " + str(env.image)) if env.ok else f"failed: {env.limitation}\n{env.log_tail}")
    return 0 if env.ok else 1


def _cases() -> int:
    from verifix.evals.cases import load_cases

    for c in load_cases():
        print(f"{c.id:24} {c.category:18} {c.title}")
    return 0


async def _eval(args: argparse.Namespace) -> int:
    from verifix.evals.cases import load_cases
    from verifix.evals.grader import Grader
    from verifix.evals.runner import (
        AgentSolver,
        NoopSolver,
        ReferenceSolver,
        Solver,
        run_benchmark,
        save_run,
        validate_ruler,
    )
    from verifix.wiring import build_sandbox

    settings = get_settings()
    cases = load_cases(args.cases.split(",") if args.cases else None)
    sandbox = build_sandbox(settings)
    ok, why = sandbox.available()
    if not ok:
        print(f"Docker is not available: {why}")
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="verifix-bench-"))
    grader = Grader(sandbox, scratch / "grader")

    if args.validate_ruler:
        result = await validate_ruler(cases, grader, scratch)
        _print(result)
        return 0 if result["ok"] else 1

    solver: Solver
    sm = None
    if args.solver == "reference":
        solver = ReferenceSolver(scratch / "ref")
    elif args.solver == "noop":
        solver = NoopSolver()
    else:
        if args.mode in ("live", "record") and not args.confirm_cost:
            print(
                f"--mode {args.mode} calls the Anthropic API for {len(cases)} case(s) "
                f"(budget up to ${args.max_cost:.2f} each). Re-run with --confirm-cost."
            )
            return 2
        if args.mode in ("record", "replay") and not args.cassettes:
            print("--cassettes DIR is required for record/replay")
            return 2
        from verifix.core.db import get_sessionmaker
        from verifix.evals.cassette import RecordingLLM, ReplayLLM
        from verifix.wiring import build_llm

        sm = get_sessionmaker()
        cassettes = Path(args.cassettes) if args.cassettes else None

        # A fresh client per case: each gets its own retry budget and circuit breaker,
        # so one case's provider outage cannot fail every case after it.
        def llm_for(case):  # type: ignore[no-untyped-def]
            if args.mode == "replay":
                assert cassettes is not None  # noqa: S101 - checked above
                return ReplayLLM(cassettes / f"{case.id}.jsonl")
            if args.mode == "record":
                assert cassettes is not None  # noqa: S101
                return RecordingLLM(build_llm(settings), cassettes / f"{case.id}.jsonl")
            return build_llm(settings)

        from verifix.agent.policy import Budget

        solver = AgentSolver(
            sessionmaker=sm,
            settings=settings,
            sandbox=sandbox,
            llm_for=llm_for,
            budget=Budget(max_model_calls=30, max_cost_usd=args.max_cost, max_minutes=10),
        )

    def progress(r):  # type: ignore[no-untyped-def]
        mark = "PASS" if r.success else "FAIL"
        extra = f" agent={r.solve.get('agent_status')}" if r.solve.get("agent_status") else ""
        print(f"  {mark} {r.case_id}{extra} {r.grade.get('note', '')}".rstrip(), flush=True)

    print(f"solver={solver.name} cases={len(cases)}")
    report = await run_benchmark(solver, cases, grader, scratch / "work", on_result=progress)
    _print(report.summary)
    out = {"solver": report.solver, "summary": report.summary, "results": report.results}
    if args.out:
        target = Path(args.out)
        text = json.dumps(out, indent=2, default=str)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, text, encoding="utf-8")
        print(f"wrote {args.out}")
    if args.save:
        if sm is None:
            from verifix.core.db import get_sessionmaker

            sm = get_sessionmaker()
        mode = args.mode if args.solver == "agent" else "scripted"
        rid = await save_run(
            sm,
            name=args.name or f"{solver.name}-{mode}-{uuid.uuid4().hex[:6]}",
            mode=mode,
            config={"solver": solver.name, "cases": [c.id for c in cases], "mode": mode},
            report=report,
        )
        print(f"saved eval run {rid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="verifix")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-sandbox", help="build the sandbox runtime image once")
    sub.add_parser("cases", help="list benchmark cases")
    e = sub.add_parser("eval", help="run the reliability benchmark")
    e.add_argument("--solver", choices=["reference", "noop", "agent"], default="reference")
    e.add_argument("--mode", choices=["live", "record", "replay"], default="replay")
    e.add_argument("--cases", help="comma-separated case ids (default: all)")
    e.add_argument("--cassettes", help="directory of <case>.jsonl cassettes")
    e.add_argument("--confirm-cost", action="store_true", help="allow spending API credit")
    e.add_argument("--max-cost", type=float, default=1.0, help="USD budget per case")
    e.add_argument("--out", help="write the full report as JSON")
    e.add_argument("--save", action="store_true", help="store the run in Postgres (eval_runs)")
    e.add_argument("--name", help="name for the saved run")
    e.add_argument("--validate-ruler", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "setup-sandbox":
        return asyncio.run(_setup_sandbox())
    if args.cmd == "cases":
        return _cases()
    return asyncio.run(_eval(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""
M7 reliability benchmark.

Unit tests need nothing. The ruler test and the agent tests use the real Docker sandbox
(and Postgres for the agent), and skip cleanly where those are missing.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.test_workspace_and_sandbox import SANDBOX_IMAGE, docker
from verifix.core.config import Settings
from verifix.evals.cases import HIDDEN_PREFIX, load_cases, materialise, reference_patch
from verifix.evals.cassette import CassetteMismatch, RecordingLLM, ReplayLLM
from verifix.evals.grader import Grader, is_test_path, patch_files
from verifix.evals.runner import (
    AgentSolver,
    CaseResult,
    NoopSolver,
    run_benchmark,
    save_run,
    summarise,
    validate_ruler,
)
from verifix.llm.fake import FakeLLM, text_response, tool_response
from verifix.sandbox.docker import DockerSandbox

# ------------------------------------------------------------------ cases


def test_cases_are_well_formed() -> None:
    cases = load_cases()
    assert len(cases) == 12
    assert len({c.id for c in cases}) == 12
    assert len({c.category for c in cases}) >= 11
    for c in cases:
        assert c.request and c.expected_files
        assert c.hidden_files() and all(h.startswith(HIDDEN_PREFIX) for h in c.hidden_files())
        for f in c.expected_files:
            assert (c.repo_dir / f).is_file() and (c.fix_dir / f).is_file()
        public = c.public()
        assert "hidden" not in str(public) and "fix" not in public


def test_load_unknown_case() -> None:
    with pytest.raises(KeyError):
        load_cases(["nope"])


def test_reference_patch_touches_only_expected_files(tmp_path: Path) -> None:
    for c in load_cases():
        patch = reference_patch(c, tmp_path)
        assert patch_files(patch) == c.expected_files


def test_materialise_is_a_clean_git_repo(tmp_path: Path) -> None:
    from verifix.repos.gitcli import run_git

    c = load_cases(["sql-injection"])[0]
    repo = materialise(c, tmp_path / "r")
    assert run_git(repo, ["status", "--porcelain"]).text == ""
    assert not (repo / "tests" / "test_verifix_hidden.py").exists()


def test_patch_helpers() -> None:
    patch = "diff --git a/app/x.py b/app/x.py\n+x\ndiff --git a/tests/test_y.py b/tests/test_y.py\n"
    assert patch_files(patch) == ["app/x.py", "tests/test_y.py"]
    assert is_test_path("tests/test_y.py") and is_test_path("pkg/test_a.py")
    assert is_test_path("conftest.py") and not is_test_path("app/testing_utils.py")


def _result(success: bool, **solve: Any) -> CaseResult:
    return CaseResult(
        "c",
        "cat",
        {"success": success, "patch_applies": True, "regressions": [], "patch_precision": 1.0},
        {"tool_calls": 10, "tool_errors": 1, "cost_usd": 0.1, "duration_s": 2.0, **solve},
        false_verified=solve.get("agent_status") == "verified" and not success,
    )


def test_summary_metrics() -> None:
    s = summarise(
        [
            _result(True, agent_status="verified", evidence_coverage=1.0),
            _result(False, agent_status="verified", evidence_coverage=0.5),
            _result(False, agent_status="fixed_unverified"),
        ]
    )
    assert s["cases"] == 3 and s["successes"] == 1 and s["success_rate"] == 0.333
    assert s["claimed_verified"] == 2 and s["false_verified"] == 1
    assert s["verified_precision"] == 0.5
    assert s["tool_reliability"] == 0.9
    assert s["mean_evidence_coverage"] == 0.75
    assert s["total_cost_usd"] == 0.3


# ------------------------------------------------------------------ cassettes


async def test_cassette_record_and_replay(tmp_path: Path) -> None:
    inner = FakeLLM([tool_response("list_repository_files", {}), text_response("done")])
    path = tmp_path / "c.jsonl"
    rec = RecordingLLM(inner, path)
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    a = await rec.respond(system="s", messages=msgs)
    b = await rec.respond(system="s", messages=[*msgs, {"role": "assistant", "content": "x"}])

    rep = ReplayLLM(path)
    a2 = await rep.respond(system="other", messages=msgs)
    b2 = await rep.respond(system="s", messages=[*msgs, {"role": "assistant", "content": "x"}])
    assert a2 == a and b2 == b
    with pytest.raises(CassetteMismatch):
        await rep.respond(system="s", messages=msgs)

    drift = ReplayLLM(path)
    with pytest.raises(CassetteMismatch, match="diverged"):
        await drift.respond(system="s", messages=[*msgs, *msgs])


# ------------------------------------------------------------------ the ruler (real docker)


@docker
async def test_ruler_reference_scores_one_noop_scores_zero(tmp_path: Path) -> None:
    sandbox = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    grader = Grader(sandbox, tmp_path / "grader")
    result = await validate_ruler(load_cases(), grader, tmp_path)
    assert result["ok"], result
    assert all(result["hidden_tests_fail_on_bug"].values())


@docker
async def test_grader_catches_regressions_and_bad_patches(tmp_path: Path) -> None:
    sandbox = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    grader = Grader(sandbox, tmp_path / "grader")
    case = load_cases(["discount-rounding"])[0]
    # "Fixes" the hidden cases by breaking the visible ones: returns the price unchanged
    # when percent is 0..100 -> visible test_whole_numbers now fails = regression.
    ref = reference_patch(case, tmp_path)
    broken = ref.replace(
        "+    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))",
        "+    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)) + (percent == 0)",
    )
    g = await grader.grade(case, broken)
    assert g.patch_applies and not g.success
    assert "tests/test_pricing.py::test_whole_numbers" in g.regressions

    junk = await grader.grade(case, "diff --git a/app/pricing.py b/app/pricing.py\nnonsense\n")
    assert not junk.patch_applies and not junk.success and "does not apply" in junk.note


# ------------------------------------------------------------------ the real agent loop


def call(name: str, args: dict[str, Any]) -> Any:
    return tool_response(name, args, call_id=f"toolu_{uuid.uuid4().hex[:10]}")


REPRO = """import pytest

from app.auth import LoginError, login


def test_missing_email():
    with pytest.raises(LoginError):
        login({"password": "x"})
"""

HONEST_FIX = [
    call("run_tests", {"targets": [], "purpose": "baseline"}),
    call("create_file", {"path": "tests/test_missing_email.py", "content": REPRO, "reason": "r"}),
    call("run_tests", {"targets": ["tests/test_missing_email.py"], "purpose": "reproduce"}),
    call(
        "edit_file",
        {
            "path": "app/auth.py",
            "old_text": '    email = normalise_email(payload["email"])',
            "new_text": '    raw = payload.get("email")\n'
            "    if not raw or not str(raw).strip():\n"
            '        raise LoginError("email required")\n'
            "    email = normalise_email(raw)",
            "reason": "validate before use",
        },
    ),
    call("run_tests", {"targets": [], "purpose": "full suite after the fix"}),
    call(
        "finish",
        {
            "outcome": "fixed",
            "summary": "login validates email",
            "claims": [
                {"statement": "missing email reproduced as KeyError", "evidence": [3]},
                {"statement": "suite passes after the fix", "evidence": [5]},
            ],
            "uncertainty": "none",
        },
    ),
]

# Claims "fixed" after a cosmetic edit. The visible suite passes, so the agent's own
# report says verified - the hidden tests disagree. This is exactly what the
# false_verified metric exists to catch.
OVERCONFIDENT = [
    call(
        "edit_file",
        {
            "path": "app/pagination.py",
            "old_text": "    start = page * per_page",
            "new_text": "    start = page * per_page  # pages are 1-based",
            "reason": "clarify",
        },
    ),
    call("run_tests", {"targets": [], "purpose": "check"}),
    call(
        "finish",
        {
            "outcome": "fixed",
            "summary": "fixed pagination",
            "claims": [{"statement": "tests pass", "evidence": [2]}],
            "uncertainty": "none",
        },
    ),
]


@pytest.mark.db
@docker
async def test_agent_solver_end_to_end_with_record_replay(
    db_sessionmaker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIFIX_WORKSPACES_DIR", str(tmp_path / "ws"))
    settings = Settings(_env_file=None)
    sandbox = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    grader = Grader(sandbox, tmp_path / "grader")
    cases = load_cases(["login-missing-email", "pagination-off-by-one"])
    scripts = {"login-missing-email": HONEST_FIX, "pagination-off-by-one": OVERCONFIDENT}
    cassettes = tmp_path / "cassettes"

    recording = AgentSolver(
        sessionmaker=db_sessionmaker,
        settings=settings,
        sandbox=sandbox,
        llm_for=lambda c: RecordingLLM(FakeLLM(list(scripts[c.id])), cassettes / f"{c.id}.jsonl"),
    )
    live = await run_benchmark(recording, cases, grader, tmp_path / "work1")
    by_id = {r["case_id"]: r for r in live.results}

    honest = by_id["login-missing-email"]
    assert honest["success"], honest["grade"]
    assert honest["solve"]["agent_status"] == "verified" and not honest["false_verified"]
    assert honest["solve"]["evidence_coverage"] == 1.0
    assert honest["grade"]["patch_precision"] == 1.0

    over = by_id["pagination-off-by-one"]
    assert not over["success"] and over["solve"]["agent_status"] == "verified"
    assert over["false_verified"]
    assert live.summary["false_verified"] == 1 and live.summary["verified_precision"] == 0.5

    replaying = AgentSolver(
        sessionmaker=db_sessionmaker,
        settings=settings,
        sandbox=sandbox,
        llm_for=lambda c: ReplayLLM(cassettes / f"{c.id}.jsonl"),
    )
    replay = await run_benchmark(replaying, cases, grader, tmp_path / "work2")
    assert [r["success"] for r in replay.results] == [r["success"] for r in live.results]
    assert replay.summary["false_verified"] == 1

    rid = await save_run(db_sessionmaker, name="t", mode="replay", config={}, report=replay)
    from verifix.models import EvalRun

    async with db_sessionmaker() as s:
        row = await s.get(EvalRun, rid)
        assert row is not None and row.summary["state"] == "finished"
        assert len(row.results) == 2


# ------------------------------------------------------------------ API


@pytest.mark.db
@docker
async def test_evals_api(api, db_sessionmaker) -> None:  # type: ignore[no-untyped-def]
    api.app.state.eval_sandbox = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    api.app.state.eval_sessionmaker = db_sessionmaker

    cases = (await api.get("/evals/cases")).json()
    assert len(cases) == 12 and "test_verifix_hidden" not in str(cases)

    r = await api.post("/evals", json={"solver": "agent"})
    assert r.status_code == 422 and "confirm_cost" in r.text
    assert (await api.post("/evals", json={"solver": "noop", "cases": ["x"]})).status_code == 422

    r = await api.post(
        "/evals", json={"solver": "reference", "cases": ["mutable-default", "sql-injection"]}
    )
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    assert r.json()["summary"]["state"] == "running"
    for _ in range(120):
        run = (await api.get(f"/evals/{rid}")).json()
        if run["summary"].get("state") != "running":
            break
        await asyncio.sleep(0.5)
    assert run["summary"]["state"] == "finished", run["summary"]
    assert run["summary"]["success_rate"] == 1.0 and len(run["results"]) == 2
    listed = (await api.get("/evals")).json()
    assert listed[0]["id"] == rid and listed[0]["results"] is None


async def test_noop_solver_gives_empty_patch(tmp_path: Path) -> None:
    c = load_cases(["mutable-default"])[0]
    assert (await NoopSolver().solve(c, tmp_path)).patch == ""

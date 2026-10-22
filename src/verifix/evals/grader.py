"""
Grading a patch against a case - independently of anything the agent said.

    buggy repo ──git apply patch──▶ patched repo ──copy hidden tests──▶ graded tree
                                                                         │
                         sandbox: hidden tests ◀──────────────────────────┤
                         sandbox: whole suite  ◀──────────────────────────┘

success  = the patch applies AND every hidden test passes AND no visible test that passed
           on the buggy code fails now (no regressions)

The agent's own report never enters this function. That separation is what lets the
benchmark measure "false verified": the agent claimed `verified` but the grader says no.
"""

import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verifix.evals.cases import Case, copy_tree, materialise
from verifix.repos.gitcli import run_git
from verifix.sandbox.base import Sandbox, TestRunResult

_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.M)


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith(("tests/", "test/"))
        or "/tests/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for a, b in _DIFF_FILE.findall(patch):
        for p in (a, b):
            if p not in files and p != "/dev/null":
                files.append(p)
    return files


def open_permissions(root: Path) -> None:
    """The sandbox runs as uid 1000; make the graded tree readable to it."""
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)  # noqa: S103 - read/execute for the sandbox user only
        for d in dirnames:
            os.chmod(os.path.join(dirpath, d), 0o755)  # noqa: S103
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o644)


@dataclass
class Grade:
    case_id: str
    patch_applies: bool
    hidden_passed: bool
    success: bool
    regressions: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    source_files_changed: list[str] = field(default_factory=list)
    patch_precision: float | None = None  # expected ∩ changed / changed (source files only)
    hidden_run: dict[str, Any] | None = None
    suite_run: dict[str, Any] | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_summary(r: TestRunResult) -> dict[str, Any]:
    return {
        "status": r.status,
        "exit_code": r.exit_code,
        "passed": r.passed,
        "failed": r.failed,
        "errors": r.errors,
        "failing_tests": r.failing_tests,
        "duration_ms": r.duration_ms,
        "command": r.command,
        "limitation": r.limitation,
        "output_tail": r.output_tail[-2000:],
    }


class Grader:
    def __init__(self, sandbox: Sandbox, scratch: Path) -> None:
        self.sandbox = sandbox
        self.scratch = scratch
        self._baseline: dict[str, set[str]] = {}

    async def run_tests(self, workdir: Path, targets: list[str]) -> TestRunResult:
        env = await self.sandbox.ensure_environment(workdir, allow_install=False)
        if not env.ok:
            return TestRunResult("blocked", None, 0, "", limitation=env.limitation)
        return await self.sandbox.run_pytest(
            workdir, targets=targets, keyword=None, image=env.image, run_id=uuid.uuid4().hex[:10]
        )

    async def baseline_passing(self, case: Case) -> set[str]:
        """Visible tests that pass on the buggy code (the regression reference)."""
        if case.id not in self._baseline:
            repo = materialise(case, self.scratch / f"baseline-{case.id}")
            open_permissions(repo)
            res = await self.run_tests(repo, [])
            if res.status == "blocked":
                raise RuntimeError(f"sandbox unavailable: {res.limitation}")
            self._baseline[case.id] = set(res.passing_tests)
        return self._baseline[case.id]

    async def grade(self, case: Case, patch: str) -> Grade:
        changed = patch_files(patch)
        source = [f for f in changed if not is_test_path(f)]
        precision = (
            len([f for f in source if f in case.expected_files]) / len(source) if source else None
        )
        g = Grade(
            case.id,
            patch_applies=False,
            hidden_passed=False,
            success=False,
            changed_files=changed,
            source_files_changed=source,
            patch_precision=precision,
        )
        if not patch.strip():
            g.note = "empty patch"
            return g
        baseline = await self.baseline_passing(case)
        tree = materialise(case, self.scratch / f"graded-{case.id}-{uuid.uuid4().hex[:6]}")
        try:
            applied = run_git(
                tree,
                ["apply", "--whitespace=nowarn", "-"],
                input_bytes=patch.encode("utf-8"),
                check=False,
            )
            if applied.code != 0:
                g.note = f"patch does not apply: {applied.stderr.strip()[:300]}"
                return g
            g.patch_applies = True
            copy_tree(case.hidden_dir, tree)  # overwrites any same-named file the agent wrote
            open_permissions(tree)
            hidden = await self.run_tests(tree, case.hidden_files())
            suite = await self.run_tests(tree, [])
            g.hidden_run, g.suite_run = _run_summary(hidden), _run_summary(suite)
            if "blocked" in (hidden.status, suite.status):
                g.note = f"sandbox blocked: {hidden.limitation or suite.limitation}"
                return g
            g.hidden_passed = hidden.status == "passed"
            now_passing = set(suite.passing_tests)
            g.regressions = sorted(t for t in baseline if t not in now_passing)
            g.success = g.hidden_passed and not g.regressions
            if not g.success:
                g.note = (
                    "hidden tests failed: " + ", ".join(hidden.failing_tests[:5])
                    if not g.hidden_passed
                    else "regressions: " + ", ".join(g.regressions[:5])
                )
            return g
        finally:
            shutil.rmtree(tree, ignore_errors=True)

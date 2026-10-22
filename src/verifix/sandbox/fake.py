"""A scripted sandbox for agent tests: returns queued results and records what was asked."""

from pathlib import Path

from verifix.sandbox.base import EnvResult, TestRunResult, validate_keyword, validate_target


class FakeSandbox:
    def __init__(self, results: list[TestRunResult] | None = None, *, needs_install: bool = False):
        self.results = list(results or [])
        self.needs_install = needs_install
        self.runs: list[dict] = []
        self.installs = 0

    def available(self) -> tuple[bool, str]:
        return True, "fake"

    async def ensure_environment(self, workdir: Path, *, allow_install: bool) -> EnvResult:
        if self.needs_install and not allow_install:
            return EnvResult(False, None, False, True, limitation="dependencies not installed")
        if self.needs_install:
            self.installs += 1
            self.needs_install = False
            return EnvResult(True, "fake-env", True, False, log_tail="installed")
        return EnvResult(True, "fake-base", False, False)

    async def run_pytest(
        self,
        workdir: Path,
        *,
        targets: list[str],
        keyword: str | None,
        image: str | None,
        run_id: str,
    ) -> TestRunResult:
        targets = [validate_target(t) for t in targets]
        if keyword:
            validate_keyword(keyword)
        self.runs.append({"workdir": str(workdir), "targets": targets, "keyword": keyword})
        if not self.results:
            raise AssertionError("FakeSandbox has no scripted result left")
        return self.results.pop(0)


def passed(n: int = 1, tests: list[str] | None = None) -> TestRunResult:
    return TestRunResult("passed", 0, 120, "pytest -q", passed=n, passing_tests=tests or [])


def failed(
    n: int = 1, tests: list[str] | None = None, output: str = "E   KeyError: 'email'"
) -> TestRunResult:
    return TestRunResult(
        "failed", 1, 130, "pytest -q", failed=n, failing_tests=tests or [], output_tail=output
    )

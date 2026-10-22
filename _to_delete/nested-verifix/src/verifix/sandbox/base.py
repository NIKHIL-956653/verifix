"""
Sandbox contract and the result types the rest of the system trusts.

The verification engine's whole job is to turn "the model thinks the tests pass" into
"this command ran, in this environment, and exited with this code". So results carry the
exact command, exit code, duration, environment and limitations, and `status` is derived
from the exit code - never from parsing optimistic text.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

RunStatus = Literal["passed", "failed", "error", "no_tests", "timeout", "blocked"]

# pytest exit codes: https://docs.pytest.org/en/stable/reference/exit-codes.html
_PYTEST_EXIT = {0: "passed", 1: "failed", 2: "error", 3: "error", 4: "error", 5: "no_tests"}

_TARGET = re.compile(r"^[A-Za-z0-9_./\-]+(::[A-Za-z0-9_\-\[\].,=: ]+)*$")
_KEYWORD = re.compile(r"^[A-Za-z0-9_ .\-()]+$")


class InvalidTestArgs(ValueError):
    pass


def validate_target(target: str) -> str:
    """A test path or node id. Never an option, never outside the repo."""
    t = target.strip().replace("\\", "/")
    if not t or t.startswith("-") or len(t) > 300 or not _TARGET.match(t):
        raise InvalidTestArgs(f"invalid test target {target!r}")
    path = t.split("::", 1)[0]
    if path.startswith("/") or ".." in path.split("/"):
        raise InvalidTestArgs(
            f"test target must be a relative path inside the repository: {target!r}"
        )
    return t


def validate_keyword(keyword: str) -> str:
    k = keyword.strip()
    if not k or len(k) > 100 or not _KEYWORD.match(k):
        raise InvalidTestArgs(f"invalid -k expression {keyword!r}")
    return k


def status_from_exit(code: int | None) -> RunStatus:
    if code is None:
        return "timeout"
    return _PYTEST_EXIT.get(code, "error")  # type: ignore[return-value]


@dataclass
class TestRunResult:
    status: RunStatus
    exit_code: int | None
    duration_ms: int
    command: str  # display form of the argv that ran inside the sandbox
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failing_tests: list[str] = field(default_factory=list)
    passing_tests: list[str] = field(default_factory=list)
    output_tail: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    limitation: str | None = None

    __test__ = False  # not a pytest test class

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary_line(self) -> str:
        if self.status in ("blocked", "timeout"):
            return f"tests {self.status}: {self.limitation or ''}".strip()
        return (
            f"tests {self.status} (exit {self.exit_code}): {self.passed} passed, "
            f"{self.failed} failed, {self.errors} errors, {self.skipped} skipped "
            f"in {self.duration_ms / 1000:.1f}s"
        )


@dataclass
class EnvResult:
    ok: bool
    image: str | None
    built: bool  # True if dependencies were installed into a new image
    needs_install: bool  # dependencies exist and have not been installed yet
    log_tail: str = ""
    limitation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Sandbox(Protocol):
    def available(self) -> tuple[bool, str]: ...

    async def ensure_environment(self, workdir: Path, *, allow_install: bool) -> EnvResult: ...

    async def run_pytest(
        self,
        workdir: Path,
        *,
        targets: list[str],
        keyword: str | None,
        image: str | None,
        run_id: str,
    ) -> TestRunResult: ...

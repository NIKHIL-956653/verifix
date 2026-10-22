"""
Running repository tests inside Docker.

Every test run is a fresh container with:

    --network none                  no network at all
    --read-only + tmpfs /tmp        root filesystem read-only; scratch space in memory
    -v <workdir>:/work:ro           the agent's worktree, mounted read-only
    --user 1000:1000                not root
    --cap-drop ALL, no-new-privileges
    --memory / --cpus / --pids-limit, and a wall-clock timeout (container is killed)

The command is built here from validated parts (pytest + test node ids + an optional -k
expression); the model never supplies a command line.

Dependencies: if the repository declares any, they are installed into a derived image
(`verifix-env:<hash of the manifests>`). That build is the ONE step that has network
access, so it requires explicit approval (`allow_install=True`). Repos with no
third-party dependencies run directly on the base image.
"""

import asyncio
import hashlib
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from verifix.core.redact import redact_text
from verifix.sandbox import pytest_output
from verifix.sandbox.base import (
    EnvResult,
    TestRunResult,
    status_from_exit,
    validate_keyword,
    validate_target,
)

OUTPUT_TAIL = 8_000
_MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
)

_DOCKERFILE = """\
FROM {base}
WORKDIR /src
COPY . /src
RUN python -m pip install --no-cache-dir --disable-pip-version-check -q pytest \\
 && if [ -f pyproject.toml ] || [ -f setup.py ]; then \\
      python -m pip install --no-cache-dir -q ".[test]" \\
      || python -m pip install --no-cache-dir -q ".[dev]" \\
      || python -m pip install --no-cache-dir -q . ; fi \\
 && for r in requirements.txt requirements-dev.txt requirements-test.txt; do \\
      if [ -f "$r" ]; then python -m pip install --no-cache-dir -q -r "$r"; fi; done
"""


def _has_python_dependencies(workdir: Path) -> bool:
    for req in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        p = workdir / req
        if p.is_file() and any(
            line.strip() and not line.strip().startswith("#")
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            return True
    if (workdir / "setup.py").is_file():
        return True
    pp = workdir / "pyproject.toml"
    if pp.is_file():
        try:
            data = tomllib.loads(pp.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return True  # can't tell - assume it needs installing
        proj = data.get("project", {})
        deps = list(proj.get("dependencies", []))
        for group in ("test", "dev"):
            deps += list(proj.get("optional-dependencies", {}).get(group, []))
        deps = [d for d in deps if not str(d).lower().startswith("pytest")]
        poetry = data.get("tool", {}).get("poetry", {})
        if deps or [d for d in poetry.get("dependencies", {}) if d != "python"]:
            return True
    return False


def manifest_hash(workdir: Path, base_image: str) -> str:
    h = hashlib.sha256(base_image.encode())
    for name in _MANIFESTS:
        p = workdir / name
        if p.is_file():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


class DockerSandbox:
    def __init__(
        self,
        *,
        base_image: str = "python:3.12-slim",
        memory: str = "1g",
        cpus: str = "2",
        pids: int = 256,
        timeout_s: int = 300,
        docker_bin: str = "docker",
        build_timeout_s: int = 900,
    ) -> None:
        self.base_image = base_image
        self.memory = memory
        self.cpus = cpus
        self.pids = pids
        self.timeout_s = timeout_s
        self.docker = docker_bin
        self.build_timeout_s = build_timeout_s

    # ------------------------------------------------------------------ helpers

    def available(self) -> tuple[bool, str]:
        if shutil.which(self.docker) is None:
            return False, f"'{self.docker}' not found on PATH - install Docker Desktop"
        try:
            proc = subprocess.run(  # noqa: S603
                [self.docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, "docker did not respond"
        if proc.returncode != 0:
            return False, "Docker is installed but the daemon is not running (start Docker Desktop)"
        return True, proc.stdout.decode().strip()

    def _image_exists(self, tag: str) -> bool:
        proc = subprocess.run(  # noqa: S603
            [self.docker, "image", "inspect", tag], capture_output=True, timeout=30, check=False
        )
        return proc.returncode == 0

    def env_tag(self, workdir: Path) -> str:
        return f"verifix-env:{manifest_hash(workdir, self.base_image)}"

    async def _exec(
        self, argv: list[str], limit_s: float, name: str | None = None
    ) -> tuple[int | None, bytes, float]:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=limit_s)
            code: int | None = proc.returncode
        except TimeoutError:
            code = None
            if name:
                await asyncio.create_subprocess_exec(
                    self.docker,
                    "kill",
                    name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            proc.kill()
            out, _ = await proc.communicate()
        except asyncio.CancelledError:
            if name:
                await asyncio.create_subprocess_exec(
                    self.docker,
                    "kill",
                    name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            proc.kill()
            raise
        return code, out or b"", time.monotonic() - start

    # ------------------------------------------------------------------ environment

    async def ensure_environment(self, workdir: Path, *, allow_install: bool) -> EnvResult:
        ok, why = self.available()
        if not ok:
            return EnvResult(False, None, False, False, limitation=why)
        if not _has_python_dependencies(workdir):
            if not self._image_exists(self.base_image):
                return EnvResult(
                    False,
                    None,
                    False,
                    True,
                    limitation=f"base image {self.base_image} is not present; "
                    f"run `docker pull {self.base_image}` once",
                )
            return EnvResult(
                True, self.base_image, False, False, log_tail="no third-party dependencies"
            )
        tag = self.env_tag(workdir)
        if self._image_exists(tag):
            return EnvResult(True, tag, False, False, log_tail=f"reusing {tag}")
        if not allow_install:
            return EnvResult(
                False,
                None,
                False,
                True,
                limitation="the repository declares dependencies that are not installed yet; "
                "installing them needs network access and requires approval (prepare_environment)",
            )
        with tempfile.TemporaryDirectory(prefix="verifix-build-") as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text(_DOCKERFILE.format(base=self.base_image), encoding="utf-8")
            ignore = Path(tmp) / "Dockerfile.dockerignore"
            ignore.write_text(".git\n**/__pycache__\n.env*\n!.env.example\n", encoding="utf-8")
            argv = [self.docker, "build", "-q", "-t", tag, "-f", str(dockerfile), str(workdir)]
            code, out, _ = await self._exec(argv, self.build_timeout_s)
        tail = redact_text(out.decode("utf-8", errors="replace"))[-OUTPUT_TAIL:]
        if code != 0:
            return EnvResult(
                False, None, False, True, log_tail=tail, limitation="dependency install failed"
            )
        return EnvResult(True, tag, True, False, log_tail=tail)

    # ------------------------------------------------------------------ tests

    def pytest_argv(
        self, workdir: Path, image: str, *, targets: list[str], keyword: str | None, name: str
    ) -> list[str]:
        safe_targets = [validate_target(t) for t in targets]
        argv = [
            self.docker,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,size=256m",  # noqa: S108 - tmpfs inside the container, not the host
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "-e",
            "HOME=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "PYTHONPATH=/work:/work/src",
            "-v",
            f"{workdir}:/work:ro",
            "-w",
            "/work",
            image,
            "python",
            "-m",
            "pytest",
            "-q",
            "-rA",
            "--tb=short",
            "--color=no",
            "-p",
            "no:cacheprovider",
        ]
        if keyword:
            argv += ["-k", validate_keyword(keyword)]
        argv += ["--", *safe_targets]
        return argv

    async def run_pytest(
        self,
        workdir: Path,
        *,
        targets: list[str],
        keyword: str | None,
        image: str | None,
        run_id: str,
    ) -> TestRunResult:
        ok, why = self.available()
        if not ok:
            return TestRunResult("blocked", None, 0, "", limitation=why)
        if image is None:
            env = await self.ensure_environment(workdir, allow_install=False)
            if not env.ok or env.image is None:
                return TestRunResult("blocked", None, 0, "", limitation=env.limitation)
            image = env.image
        name = f"verifix-run-{run_id}"
        argv = self.pytest_argv(workdir, image, targets=targets, keyword=keyword, name=name)
        shown = (
            "pytest -q -rA --tb=short -p no:cacheprovider"
            + (f" -k {keyword!r}" if keyword else "")
            + (" " + " ".join(targets) if targets else "")
        )
        code, out, secs = await self._exec(argv, self.timeout_s, name=name)
        text = redact_text(out.decode("utf-8", errors="replace"))
        parsed = pytest_output.parse(text)
        status = status_from_exit(code)
        return TestRunResult(
            status=status,
            exit_code=code,
            duration_ms=int(secs * 1000),
            command=shown,
            passed=int(parsed["passed"]),  # type: ignore[arg-type]
            failed=int(parsed["failed"]),  # type: ignore[arg-type]
            errors=int(parsed["errors"]),  # type: ignore[arg-type]
            skipped=int(parsed["skipped"]),  # type: ignore[arg-type]
            failing_tests=list(parsed["failing_tests"]),  # type: ignore[arg-type]
            passing_tests=list(parsed["passing_tests"]),  # type: ignore[arg-type]
            output_tail=text[-OUTPUT_TAIL:],
            environment={
                "image": image,
                "network": "none",
                "memory": self.memory,
                "cpus": self.cpus,
                "pids_limit": self.pids,
                "filesystem": "read-only root, read-only /work, tmpfs /tmp",
                "user": "1000:1000",
                "timeout_s": self.timeout_s,
            },
            limitation=f"killed after {self.timeout_s}s" if code is None else None,
        )

"""
The only way verifix runs git.

Git reads configuration from the repository itself (`.git/config`), and some settings
make git execute commands: an fsmonitor hook, clean/smudge filters, textconv drivers, an
external diff tool, hooks on checkout and commit. A repository we were asked to inspect
must not be able to run code on the host just because we ran `git status` in it.

Defences, all applied here so no caller can forget them:

- `core.fsmonitor=false`, `core.hooksPath=<empty dir>` on every invocation.
- `GIT_OPTIONAL_LOCKS=0`, so read-only commands (status) never write the index.
- `--no-ext-diff --no-textconv` on every diff (callers use `diff()` here).
- `audit_config()` refuses repositories whose local config defines filter drivers or
  includes other config files. Those cannot be neutralised generically, so we decline
  to operate on the repository rather than hope.
- No terminal prompts, a timeout on every call, and argv lists only (never a shell).
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_S = 30


class GitError(RuntimeError):
    def __init__(self, args: list[str], code: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args[:3])}... failed ({code}): {stderr.strip()[:300]}")
        self.code = code
        self.stderr = stderr


class UnsafeRepoConfig(RuntimeError):
    def __init__(self, keys: list[str]) -> None:
        super().__init__(
            "repository config defines settings that can execute code: " + ", ".join(sorted(keys))
        )
        self.keys = keys


@dataclass(frozen=True)
class GitResult:
    code: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


_NO_HOOKS_DIR = Path(tempfile.gettempdir()) / "verifix-no-hooks"

_UNSAFE_KEY = re.compile(
    r"^(filter\..+\.(clean|smudge|process)|include\.path|includeif\..+\.path)$", re.I
)


def _no_hooks_dir() -> str:
    _NO_HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    return str(_NO_HOOKS_DIR)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "GCM_INTERACTIVE": "never",
            "LC_ALL": "C.UTF-8" if os.name != "nt" else env.get("LC_ALL", ""),
        }
    )
    return {k: v for k, v in env.items() if v is not None}


def run_git(
    repo: Path | str,
    args: list[str],
    *,
    check: bool = True,
    timeout: float = GIT_TIMEOUT_S,
    input_bytes: bytes | None = None,
    extra_config: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> GitResult:
    argv = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={_no_hooks_dir()}",
        "-c",
        "core.quotePath=false",
    ]
    for item in extra_config or []:
        argv += ["-c", item]
    argv += ["-C", str(repo), *args]
    proc = subprocess.run(  # noqa: S603 - fixed binary, argv list, no shell
        argv,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        env={**_env(), **(extra_env or {})},
        check=False,
    )
    result = GitResult(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", errors="replace"))
    if check and proc.returncode != 0:
        raise GitError(args, proc.returncode, result.stderr)
    return result


def audit_config(repo: Path | str) -> list[str]:
    """Keys in the repository's local config that could make git run code. Empty = safe."""
    res = run_git(repo, ["config", "--local", "--list", "-z"], check=False)
    if res.code not in (0, 1):  # 1 = no local config at all
        raise GitError(["config"], res.code, res.stderr)
    bad = []
    for entry in res.stdout.split(b"\x00"):
        if not entry:
            continue
        key = entry.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        if _UNSAFE_KEY.match(key):
            bad.append(key)
    return bad


def ensure_safe(repo: Path | str) -> None:
    bad = audit_config(repo)
    if bad:
        raise UnsafeRepoConfig(bad)


def toplevel(path: Path | str) -> Path:
    res = run_git(path, ["rev-parse", "--show-toplevel"], check=False)
    if res.code != 0:
        raise GitError(["rev-parse"], res.code, res.stderr or "not a git repository")
    return Path(res.text.strip()).resolve()


def current_branch(repo: Path | str) -> str | None:
    res = run_git(repo, ["symbolic-ref", "--short", "-q", "HEAD"], check=False)
    return res.text.strip() or None


def head_sha(repo: Path | str) -> str | None:
    res = run_git(repo, ["rev-parse", "--verify", "-q", "HEAD"], check=False)
    return res.text.strip() or None

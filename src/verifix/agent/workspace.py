"""
Per-task workspaces: an isolated git worktree the agent can change freely (ADR-003).

Creating one:
  1. `git worktree add` a new branch `verifix/<task>` at the user's HEAD, in a directory
     outside the repository.
  2. Copy in the user's uncommitted work - tracked changes (staged or not, as a binary
     diff) and untracked, non-ignored files - so the agent sees exactly what the user sees.
     Secret files are never copied.
  3. Commit that state in the worktree as the *baseline*. Everything the agent changes is
     measured against this commit.

The user's checkout is only ever read during this (plus git's own worktree bookkeeping in
.git/). Bringing the agent's change back is a separate, explicit step (`apply_to_source`)
that first proves the user has not changed the same files since the snapshot.
"""

import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from verifix.repos.gitcli import GitError, head_sha, run_git
from verifix.repos.paths import deny_reason

_IDENTITY = ["user.name=verifix", "user.email=verifix@localhost", "commit.gpgsign=false"]


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    path: Path
    branch: str
    baseline: str
    source_root: Path
    source_head: str


def _short(task_id: uuid.UUID | str) -> str:
    return str(task_id).replace("-", "")[:12]


def create(source_root: Path, task_id: uuid.UUID | str, workspaces_dir: Path) -> Workspace:
    source_root = Path(source_root).resolve()
    head = head_sha(source_root)
    if head is None:
        raise WorkspaceError(
            "the repository has no commits yet; commit once so there is a baseline"
        )
    short = _short(task_id)
    path = (Path(workspaces_dir).expanduser() / f"{source_root.name}-{short}").resolve()
    if path.is_relative_to(source_root):
        raise WorkspaceError("workspaces directory must be outside the repository")
    branch = f"verifix/{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise WorkspaceError(f"workspace already exists: {path}")

    run_git(source_root, ["worktree", "add", "-q", "-b", branch, str(path), head])
    try:
        tracked = run_git(
            source_root,
            ["diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"],
        ).stdout
        if tracked.strip():
            run_git(path, ["apply", "--binary", "--whitespace=nowarn", "-"], input_bytes=tracked)
        untracked = run_git(
            source_root, ["ls-files", "-z", "--others", "--exclude-standard"]
        ).stdout.split(b"\x00")
        for raw in untracked:
            rel = raw.decode("utf-8", errors="replace")
            if not rel or deny_reason(rel):
                continue
            src = source_root / rel
            if src.is_symlink() or not src.is_file():
                continue
            dst = path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        run_git(path, ["add", "-A"])
        run_git(
            path,
            [
                "commit",
                "-q",
                "--allow-empty",
                "--no-verify",
                "-m",
                "verifix baseline: HEAD + uncommitted changes",
            ],
            extra_config=_IDENTITY,
        )
        baseline = head_sha(path)
        if baseline is None:  # pragma: no cover
            raise WorkspaceError("could not record the baseline commit")
    except Exception:
        remove(source_root, path, branch)
        raise
    return Workspace(path, branch, baseline, source_root, head)


def remove(source_root: Path, path: Path, branch: str | None) -> None:
    """Delete the worktree and its branch. Never touches the user's working files."""
    run_git(source_root, ["worktree", "remove", "--force", str(path)], check=False)
    if Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)
    run_git(source_root, ["worktree", "prune"], check=False)
    if branch:
        run_git(source_root, ["branch", "-D", branch], check=False)


@contextmanager
def _scratch_index(ws_path: Path, baseline: str) -> Iterator[dict[str, str]]:
    """
    A private, throwaway git index for read-only views of the workspace.

    Diffing new (untracked) files needs them staged, but staging in the worktree's real
    index would race with the agent's own git operations and with other readers (the UI
    polls the diff while the agent works). Each view gets its own index file instead.
    """
    fd, name = tempfile.mkstemp(prefix="verifix-index-")
    os.close(fd)
    os.unlink(name)  # git wants to create it
    env = {"GIT_INDEX_FILE": name}
    try:
        run_git(ws_path, ["read-tree", baseline], extra_env=env)
        run_git(ws_path, ["add", "-A"], extra_env=env)
        yield env
    finally:
        for f in (name, name + ".lock"):
            with suppress(FileNotFoundError):
                os.unlink(f)


def changed_files(ws_path: Path, baseline: str) -> list[str]:
    with _scratch_index(ws_path, baseline) as env:
        res = run_git(ws_path, ["diff", "--cached", "--name-only", "-z", baseline], extra_env=env)
    return [p.decode("utf-8", errors="replace") for p in res.stdout.split(b"\x00") if p]


def diff(ws_path: Path, baseline: str, *, binary: bool = False) -> str:
    """The agent's changes, as a unified diff against the baseline."""
    args = ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames"]
    if binary:
        args.append("--binary")
    with _scratch_index(ws_path, baseline) as env:
        return run_git(ws_path, [*args, baseline], extra_env=env).text


def diff_stats(ws_path: Path, baseline: str) -> dict[str, int]:
    with _scratch_index(ws_path, baseline) as env:
        res = run_git(ws_path, ["diff", "--cached", "--numstat", baseline], extra_env=env).text
    added = removed = files = 0
    for line in res.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            files += 1
            added += int(parts[0]) if parts[0].isdigit() else 0
            removed += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": files, "added": added, "removed": removed}


def revert(ws_path: Path, baseline: str, rel: str) -> str:
    """Undo the agent's changes to one file. Returns 'restored' or 'deleted'."""
    exists = run_git(ws_path, ["cat-file", "-e", f"{baseline}:{rel}"], check=False).code == 0
    if exists:
        run_git(ws_path, ["checkout", baseline, "--", rel])
        return "restored"
    target = ws_path / rel
    if target.exists():
        target.unlink()
    run_git(ws_path, ["rm", "-q", "--cached", "--ignore-unmatch", "--", rel], check=False)
    return "deleted"


@dataclass
class ApplyResult:
    applied: bool
    files: list[str]
    conflicts: list[str]
    message: str


def apply_to_source(ws_path: Path, baseline: str, source_root: Path) -> ApplyResult:
    """
    Bring the agent's change into the user's checkout - only if it is safe.

    Refuses when any file the agent changed has also been changed in the user's checkout
    since the snapshot (their content no longer equals the baseline). Then `git apply
    --check` must pass before anything is written. Only the working tree is modified;
    nothing is staged or committed.
    """
    files = changed_files(ws_path, baseline)
    if not files:
        return ApplyResult(False, [], [], "no changes to apply")
    conflicts = []
    for rel in files:
        base = run_git(ws_path, ["show", f"{baseline}:{rel}"], check=False)
        current = source_root / rel
        if base.code != 0:  # file is new in the agent's change
            if current.exists():
                conflicts.append(rel)
            continue
        if not current.is_file() or current.read_bytes() != base.stdout:
            conflicts.append(rel)
    if conflicts:
        return ApplyResult(
            False,
            files,
            conflicts,
            "your checkout changed these files after the task started; nothing was applied",
        )
    patch = diff(ws_path, baseline, binary=True).encode("utf-8")
    try:
        run_git(source_root, ["apply", "--check", "--binary", "-"], input_bytes=patch)
        run_git(source_root, ["apply", "--binary", "-"], input_bytes=patch)
    except GitError as e:
        return ApplyResult(
            False, files, [], f"git apply refused the patch: {e.stderr.strip()[:300]}"
        )
    return ApplyResult(
        True, files, [], f"applied to {len(files)} file(s) in your working tree (not staged)"
    )

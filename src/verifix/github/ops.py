"""
Git operations for GitHub: clone by URL, and build + push a pull-request branch.

The PR branch is built from the commit the task started at (`source_head`) plus the
agent's patch ONLY. The task's own worktree branch is not pushed: its baseline also
contains the user's uncommitted changes, which must never leave their machine just
because they asked for a PR.
"""

import shutil
import tempfile
from pathlib import Path

from verifix.github.client import GitHubError, RepoRef, git_auth_env, parse_repo_url
from verifix.repos.gitcli import GitError, head_sha, run_git

_FALLBACK_IDENTITY = ["user.name=verifix", "user.email=verifix@users.noreply.github.com"]


def clone(ref: RepoRef, clones_dir: Path, token: str | None) -> tuple[Path, bool]:
    """Clone into <clones_dir>/<owner>__<name>. Idempotent: returns (path, cloned_now)."""
    clones_dir = Path(clones_dir).expanduser()
    clones_dir.mkdir(parents=True, exist_ok=True)
    dest = clones_dir / f"{ref.owner}__{ref.name}"
    if (dest / ".git").exists():
        return dest.resolve(), False
    if dest.exists():
        raise GitHubError(f"{dest} exists and is not a git repository")
    try:
        run_git(
            clones_dir,
            ["clone", "--quiet", "--no-recurse-submodules", "--", ref.https_url, str(dest)],
            timeout=600,
            extra_env=git_auth_env(token),
        )
    except GitError as e:
        shutil.rmtree(dest, ignore_errors=True)
        hint = " (private repository? set VERIFIX_GITHUB_TOKEN)" if not token else ""
        raise GitHubError(f"clone failed{hint}: {e.stderr.strip()[:200]}") from None
    return dest.resolve(), True


def origin(repo: Path) -> RepoRef | None:
    res = run_git(repo, ["remote", "get-url", "origin"], check=False)
    url = res.text.strip()
    if not url:
        return None
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.removeprefix("git@github.com:")
    try:
        return parse_repo_url(url)
    except GitHubError:
        return None


def build_branch(
    source_root: Path, source_head: str, patch: bytes, branch: str, message: str
) -> str:
    """Create `branch` at source_head + patch, in a throwaway worktree. Returns the commit."""
    tmp = Path(tempfile.mkdtemp(prefix="verifix-pr-"))
    wt = tmp / "wt"
    run_git(source_root, ["worktree", "add", "-q", "-b", branch, str(wt), source_head])
    ok = False
    try:
        try:
            run_git(
                wt, ["apply", "--index", "--binary", "--whitespace=nowarn", "-"], input_bytes=patch
            )
        except GitError as e:
            raise GitHubError(
                "the agent's patch does not apply to the commit the task started from "
                f"(it may depend on uncommitted changes): {e.stderr.strip()[:200]}"
            ) from None
        has_identity = run_git(wt, ["config", "user.email"], check=False).text.strip()
        run_git(
            wt,
            ["commit", "-q", "--no-verify", "-m", message],
            extra_config=None if has_identity else _FALLBACK_IDENTITY,
        )
        sha = head_sha(wt)
        if sha is None:  # pragma: no cover
            raise GitHubError("could not create the PR commit")
        ok = True
        return sha
    finally:
        run_git(source_root, ["worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        run_git(source_root, ["worktree", "prune"], check=False)
        if not ok:
            run_git(source_root, ["branch", "-D", branch], check=False)


def push(source_root: Path, branch: str, token: str | None) -> None:
    try:
        run_git(
            source_root,
            [
                "push",
                "--quiet",
                "--no-verify",
                "origin",
                f"refs/heads/{branch}:refs/heads/{branch}",
            ],
            timeout=300,
            extra_env=git_auth_env(token),
        )
    except GitError as e:
        raise GitHubError(f"push failed: {e.stderr.strip()[:200]}") from None

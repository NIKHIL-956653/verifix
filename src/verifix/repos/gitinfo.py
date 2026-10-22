"""Read-only git views: status and diff. Neither command modifies the repository."""

from dataclasses import dataclass

from verifix.repos.gitcli import current_branch, head_sha, run_git
from verifix.repos.paths import RepoJail, deny_reason

MAX_DIFF_BYTES = 300_000


@dataclass(frozen=True)
class StatusEntry:
    path: str
    index: str  # X of porcelain XY
    worktree: str  # Y of porcelain XY
    orig_path: str | None = None


@dataclass(frozen=True)
class Status:
    branch: str | None
    head: str | None
    entries: list[StatusEntry]
    hidden: int

    @property
    def dirty(self) -> bool:
        return bool(self.entries) or self.hidden > 0


@dataclass(frozen=True)
class Diff:
    text: str
    files: list[str]
    truncated: bool
    hidden: int


def status(jail: RepoJail) -> Status:
    res = run_git(jail.root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    entries: list[StatusEntry] = []
    hidden = 0
    items = res.stdout.split(b"\x00")
    i = 0
    while i < len(items):
        raw = items[i]
        i += 1
        if len(raw) < 4:
            continue
        x, y, path = chr(raw[0]), chr(raw[1]), raw[3:].decode("utf-8", errors="replace")
        orig = None
        if x in "RC":  # rename/copy: the next NUL-separated field is the original path
            orig = items[i].decode("utf-8", errors="replace") if i < len(items) else None
            i += 1
        if deny_reason(path) or (orig and deny_reason(orig)):
            hidden += 1
            continue
        entries.append(StatusEntry(path, x, y, orig))
    return Status(current_branch(jail.root), head_sha(jail.root), entries, hidden)


def diff(
    jail: RepoJail,
    *,
    base: str | None = None,
    staged: bool = False,
    paths: list[str] | None = None,
) -> Diff:
    """Unified diff of the working tree (or index) against HEAD or `base`. Secrets withheld."""
    common = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames"]
    if staged:
        common.append("--cached")
    if base:
        if base.startswith("-"):
            raise ValueError("invalid base revision")
        common.append(base)
    names = run_git(jail.root, [*common, "--name-only", "-z", "--", *(paths or [])])
    changed = [p.decode("utf-8", errors="replace") for p in names.stdout.split(b"\x00") if p]
    allowed = [p for p in changed if not deny_reason(p)]
    hidden = len(changed) - len(allowed)
    if not allowed:
        return Diff("", [], False, hidden)
    res = run_git(jail.root, [*common, "--", *(f":(literal){p}" for p in allowed)])
    data = res.stdout
    truncated = len(data) > MAX_DIFF_BYTES
    text = data[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
    return Diff(text, allowed, truncated, hidden)

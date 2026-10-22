"""
Listing and reading source files.

The file list comes from git (tracked + untracked-but-not-ignored), so `.gitignore` is
respected for free and build output / node_modules never reach the model. The secret
deny-list is then applied on top. Reads are limited to files that are in that list.
"""

import hashlib
from dataclasses import dataclass

from verifix.repos.gitcli import run_git
from verifix.repos.paths import PathRejected, RepoJail, deny_reason

MAX_FILE_BYTES = 1_000_000
MAX_READ_LINES = 400
MAX_LIST = 20_000


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int


@dataclass(frozen=True)
class FileList:
    files: list[FileEntry]
    hidden: int  # files withheld by the secret deny-list or the jail
    truncated: bool


@dataclass(frozen=True)
class ReadResult:
    path: str
    start: int
    end: int
    total_lines: int
    content: str  # numbered lines, "   12 | code"
    truncated: bool
    sha256: str  # of the whole file, so evidence can be checked later


class NotReadable(ValueError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path!r}: {reason}")
        self.path = path
        self.reason = reason


def list_files(jail: RepoJail, *, limit: int = MAX_LIST) -> FileList:
    res = run_git(jail.root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    seen: set[str] = set()
    files: list[FileEntry] = []
    hidden = 0
    for raw in res.stdout.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        if rel in seen:
            continue
        seen.add(rel)
        if deny_reason(rel):
            hidden += 1
            continue
        try:
            real, rel_real = jail.resolve(rel)
        except PathRejected:
            hidden += 1
            continue
        if not real.is_file():  # deleted from the working tree but still in the index
            continue
        files.append(FileEntry(rel_real, real.stat().st_size))
    files.sort(key=lambda f: f.path)
    truncated = len(files) > limit
    return FileList(files[:limit], hidden, truncated)


def is_ignored(jail: RepoJail, rel: str) -> bool:
    # Without --no-index, tracked files are never reported as ignored - only untracked ones.
    res = run_git(jail.root, ["check-ignore", "-q", "--", rel], check=False)
    return res.code == 0


def read_bytes_checked(jail: RepoJail, path: str) -> tuple[bytes, str]:
    real, rel = jail.resolve(path)
    if not real.is_file():
        raise NotReadable(path, "no such file")
    if is_ignored(jail, rel):
        raise NotReadable(path, "ignored by .gitignore")
    size = real.stat().st_size
    if size > MAX_FILE_BYTES:
        raise NotReadable(path, f"file is too large ({size} bytes; limit {MAX_FILE_BYTES})")
    data = real.read_bytes()
    if b"\x00" in data[:8192]:
        raise NotReadable(path, "binary file")
    return data, rel


def read_file(
    jail: RepoJail,
    path: str,
    start: int = 1,
    end: int | None = None,
    *,
    max_lines: int = MAX_READ_LINES,
) -> ReadResult:
    data, rel = read_bytes_checked(jail, path)
    lines = data.decode("utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, start)
    if total == 0:
        return ReadResult(rel, 1, 0, 0, "", False, hashlib.sha256(data).hexdigest())
    if start > total:
        raise NotReadable(path, f"start line {start} is past the end of the file ({total} lines)")
    end = total if end is None else min(max(end, start), total)
    truncated = False
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
        truncated = True
    width = len(str(end))
    numbered = "\n".join(f"{n:>{width}} | {lines[n - 1]}" for n in range(start, end + 1))
    return ReadResult(rel, start, end, total, numbered, truncated, hashlib.sha256(data).hexdigest())

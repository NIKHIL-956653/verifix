"""
Deterministic code search: exact text or a regular expression, over the jailed file list.

No embeddings (ADR-005). Results are `path:line: text`, capped, so one broad pattern
cannot flood the model's context.
"""

import fnmatch
import re
import time
from dataclasses import dataclass

from verifix.repos.files import MAX_FILE_BYTES, list_files
from verifix.repos.paths import RepoJail

MAX_RESULTS = 200
MAX_PER_FILE = 20
MAX_LINE_CHARS = (
    2_000  # lines longer than this are skipped (minified code) - also limits regex cost
)
SHOW_CHARS = 300
MAX_PATTERN = 300
TIME_BUDGET_S = 10.0


class BadPattern(ValueError):
    pass


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    files_scanned: int
    truncated: bool
    timed_out: bool


def compile_pattern(pattern: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    if not pattern:
        raise BadPattern("empty pattern")
    if len(pattern) > MAX_PATTERN:
        raise BadPattern(f"pattern longer than {MAX_PATTERN} characters")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise BadPattern(f"invalid regular expression: {e}") from None


def search(
    jail: RepoJail,
    pattern: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    path_glob: str | None = None,
    max_results: int = MAX_RESULTS,
) -> SearchResult:
    rx = compile_pattern(pattern, regex=regex, case_sensitive=case_sensitive)
    hits: list[SearchHit] = []
    scanned = 0
    deadline = time.monotonic() + TIME_BUDGET_S
    timed_out = False
    for entry in list_files(jail).files:
        if path_glob and not fnmatch.fnmatch(entry.path, path_glob):
            continue
        if entry.size > MAX_FILE_BYTES:
            continue
        if time.monotonic() > deadline:
            timed_out = True
            break
        try:
            data = (jail.root / entry.path).read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        scanned += 1
        per_file = 0
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if len(line) > MAX_LINE_CHARS:
                continue
            if rx.search(line):
                hits.append(SearchHit(entry.path, n, line.strip()[:SHOW_CHARS]))
                per_file += 1
                if len(hits) >= max_results:
                    return SearchResult(hits, scanned, True, timed_out)
                if per_file >= MAX_PER_FILE:
                    break
    return SearchResult(hits, scanned, False, timed_out)

"""Read-only tools. Everything they return becomes evidence the final report can cite."""

import asyncio
import json
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, Field

from verifix.agent import workspace
from verifix.repos import detect, files, gitinfo, search, symbols
from verifix.repos.gitcli import run_git
from verifix.repos.paths import PathRejected, RepoJail
from verifix.tools.base import EvidenceDraft, Tool, ToolContext, ToolOutcome, clip


class ListFilesIn(BaseModel):
    prefix: str | None = Field(
        None, description="Only list paths starting with this directory, e.g. 'app/'"
    )
    limit: int = Field(300, ge=1, le=2000)


async def list_repository_files(ctx: ToolContext, a: ListFilesIn, _p: object) -> ToolOutcome:
    listing = await asyncio.to_thread(files.list_files, ctx.jail)
    entries = [f for f in listing.files if not a.prefix or f.path.startswith(a.prefix)]
    shown = entries[: a.limit]
    body = "\n".join(f"{f.path} ({f.size} B)" for f in shown)
    extra = []
    if len(entries) > len(shown):
        extra.append(f"{len(entries) - len(shown)} more not shown; narrow with prefix")
    if listing.hidden:
        extra.append(f"{listing.hidden} file(s) withheld by the secret/symlink rules")
    text = body + ("\n\n" + "; ".join(extra) if extra else "")
    return ToolOutcome(
        clip(text),
        evidence=EvidenceDraft(
            "file_list",
            f"listed {len(shown)} files" + (f" under {a.prefix}" if a.prefix else ""),
            text,
            {"count": len(entries), "hidden": listing.hidden, "prefix": a.prefix},
        ),
    )


class ReadIn(BaseModel):
    path: str = Field(description="Path relative to the repository root, e.g. 'app/auth.py'")
    start_line: int = Field(1, ge=1)
    end_line: int | None = Field(
        None, ge=1, description="Inclusive. Omit to read to the end (max 400 lines per call)"
    )


async def read_source_file(ctx: ToolContext, a: ReadIn, _p: object) -> ToolOutcome:
    try:
        r = await asyncio.to_thread(files.read_file, ctx.jail, a.path, a.start_line, a.end_line)
    except PathRejected as e:
        return ToolOutcome(f"refused: {e.reason}", ok=False)
    except files.NotReadable as e:
        return ToolOutcome(f"cannot read {a.path}: {e.reason}", ok=False)
    header = f"{r.path} lines {r.start}-{r.end} of {r.total_lines}" + (
        " (truncated)" if r.truncated else ""
    )
    return ToolOutcome(
        clip(f"{header}\n{r.content}"),
        evidence=EvidenceDraft(
            "file_read",
            f"read {r.path}:{r.start}-{r.end}",
            r.content,
            {
                "path": r.path,
                "start": r.start,
                "end": r.end,
                "total_lines": r.total_lines,
                "file_sha256": r.sha256,
            },
        ),
    )


class SearchIn(BaseModel):
    pattern: str = Field(description="Text to find. Exact text unless regex=true")
    regex: bool = False
    case_sensitive: bool = False
    path_glob: str | None = Field(None, description="Restrict to matching paths, e.g. 'app/*.py'")


async def search_repository(ctx: ToolContext, a: SearchIn, _p: object) -> ToolOutcome:
    try:
        res = await asyncio.to_thread(
            search.search,
            ctx.jail,
            a.pattern,
            regex=a.regex,
            case_sensitive=a.case_sensitive,
            path_glob=a.path_glob,
        )
    except search.BadPattern as e:
        return ToolOutcome(f"bad pattern: {e}", ok=False)
    lines = [f"{h.path}:{h.line}: {h.text}" for h in res.hits]
    note = []
    if res.truncated:
        note.append("results truncated; refine the pattern")
    if res.timed_out:
        note.append("search stopped at the time limit")
    text = ("\n".join(lines) or "no matches") + ("\n" + "; ".join(note) if note else "")
    return ToolOutcome(
        clip(text),
        evidence=EvidenceDraft(
            "search",
            f"search {a.pattern!r}: {len(res.hits)} hit(s)",
            text,
            {
                "pattern": a.pattern,
                "regex": a.regex,
                "hits": len(res.hits),
                "files_scanned": res.files_scanned,
            },
        ),
    )


class FindSymbolIn(BaseModel):
    name: str = Field(
        description="Function, method or class name, e.g. 'login' or 'UserService.create'"
    )


async def find_symbol(ctx: ToolContext, a: FindSymbolIn, _p: object) -> ToolOutcome:
    found = await asyncio.to_thread(symbols.find_symbol, ctx.jail, a.name)
    lines = [
        f"{s.kind} {s.qualname} — {s.path}:{s.start}" + (f"-{s.end}" if s.end else "")
        for s in found
    ]
    text = "\n".join(lines) or f"no symbol named {a.name!r}"
    return ToolOutcome(
        text,
        evidence=EvidenceDraft(
            "symbols", f"find symbol {a.name!r}: {len(found)}", text, {"name": a.name}
        ),
    )


class NoInput(BaseModel):
    pass


async def repository_overview(ctx: ToolContext, _a: NoInput, _p: object) -> ToolOutcome:
    ov = await asyncio.to_thread(detect.overview, ctx.jail)
    text = json.dumps(asdict(ov), indent=1)
    return ToolOutcome(text, evidence=EvidenceDraft("note", "repository overview", text, {}))


async def inspect_git_status(ctx: ToolContext, _a: NoInput, _p: object) -> ToolOutcome:
    st = await asyncio.to_thread(gitinfo.status, RepoJail(ctx.source_root))
    lines = [f"{e.index}{e.worktree} {e.path}" for e in st.entries]
    text = f"branch {st.branch} @ {(st.head or '')[:12]}\n" + (
        "\n".join(lines) or "clean working tree"
    )
    if st.hidden:
        text += f"\n({st.hidden} protected file(s) not shown)"
    return ToolOutcome(
        text, evidence=EvidenceDraft("git_status", "git status of the user's checkout", text, {})
    )


class UserDiffIn(BaseModel):
    staged_only: bool = False


async def inspect_git_diff(ctx: ToolContext, a: UserDiffIn, _p: object) -> ToolOutcome:
    """The user's own uncommitted changes (what a review looks at)."""
    if ctx.workspace_path and ctx.baseline and ctx.source_head and not a.staged_only:
        text = await asyncio.to_thread(
            lambda: (
                run_git(
                    ctx.workspace_path,
                    [
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-color",
                        ctx.source_head,
                        ctx.baseline,
                    ],
                ).text
            )
        )
    else:
        d = await asyncio.to_thread(gitinfo.diff, RepoJail(ctx.source_root), staged=a.staged_only)
        text = d.text
    text = text or "no uncommitted changes"
    return ToolOutcome(
        clip(text),
        evidence=EvidenceDraft(
            "git_diff", "diff of the user's uncommitted changes", text, {"scope": "user"}
        ),
    )


async def show_my_changes(ctx: ToolContext, _a: NoInput, _p: object) -> ToolOutcome:
    if not ctx.workspace_path or not ctx.baseline:
        return ToolOutcome("this task has no workspace; nothing has been changed", ok=False)
    text = await asyncio.to_thread(workspace.diff, ctx.workspace_path, ctx.baseline)
    files_changed = await asyncio.to_thread(
        workspace.changed_files, ctx.workspace_path, ctx.baseline
    )
    body = text or "no changes yet"
    return ToolOutcome(
        clip(body),
        evidence=EvidenceDraft(
            "git_diff",
            f"agent changes: {len(files_changed)} file(s)",
            body,
            {"scope": "agent", "files": files_changed},
        ),
    )


READ_TOOLS = [
    Tool(
        "list_repository_files",
        "List files in the repository (respects .gitignore; secret files are withheld).",
        ListFilesIn,
        "read",
        list_repository_files,
    ),
    Tool(
        "read_source_file",
        "Read a file with line numbers. Use line ranges for large files. Returns evidence you can "
        "cite.",
        ReadIn,
        "read",
        read_source_file,
    ),
    Tool(
        "search_repository",
        "Search file contents (exact text, or a regular expression with regex=true). Returns "
        "path:line matches.",
        SearchIn,
        "read",
        search_repository,
    ),
    Tool(
        "find_symbol",
        "Find where a function, method or class is defined (Python exact ranges; JS/TS start "
        "lines).",
        FindSymbolIn,
        "read",
        find_symbol,
    ),
    Tool(
        "repository_overview",
        "Languages, frameworks, test runner, manifests and entry points of the repository.",
        NoInput,
        "read",
        repository_overview,
    ),
    Tool(
        "inspect_git_status",
        "git status of the user's checkout (read-only).",
        NoInput,
        "read",
        inspect_git_status,
    ),
    Tool(
        "inspect_git_diff",
        "Unified diff of the user's uncommitted changes against HEAD (read-only).",
        UserDiffIn,
        "read",
        inspect_git_diff,
    ),
]

SHOW_CHANGES = Tool(
    "show_my_changes",
    "Unified diff of every change you have made in this task's workspace so far.",
    NoInput,
    "read",
    show_my_changes,
)

ReadToolName = Literal[
    "list_repository_files",
    "read_source_file",
    "search_repository",
    "find_symbol",
    "repository_overview",
    "inspect_git_status",
    "inspect_git_diff",
]

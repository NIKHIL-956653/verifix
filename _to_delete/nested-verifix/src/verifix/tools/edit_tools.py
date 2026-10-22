"""
Changing files - only ever inside the task's worktree.

Edits are exact-text replacements rather than model-written unified diffs: models are
unreliable at producing diffs that apply, but reliable at quoting the text they want to
change. The system then produces the real unified diff with difflib (for the approval
preview) and git (for the task's overall change). A replacement must match exactly once,
so an edit can never silently land in the wrong place.

Line endings are preserved: a CRLF file stays CRLF after the edit (Windows checkouts).
"""

import asyncio
import difflib
from pathlib import Path

from pydantic import BaseModel, Field

from verifix.agent import workspace
from verifix.repos import files
from verifix.repos.paths import PathRejected
from verifix.tools.base import EvidenceDraft, Prepared, Tool, ToolContext, ToolOutcome

MAX_NEW_FILE = 200_000


def _udiff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}" if before else "/dev/null",
            tofile=f"b/{path}",
        )
    )


def _count(diff: str) -> tuple[int, int]:
    added = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return added, removed


class EditIn(BaseModel):
    path: str = Field(description="Repository-relative path of an existing file")
    old_text: str = Field(
        description=(
            "Exact text currently in the file. Must occur exactly once; include a few surrounding "
            "lines to make it unique."
        )
    )
    new_text: str = Field(description="Replacement text")
    reason: str = Field(description="One sentence: why this change")


async def prepare_edit(ctx: ToolContext, a: EditIn) -> Prepared:
    if ctx.workspace_path is None:
        return Prepared(error="this task cannot modify files")
    try:
        data, rel = await asyncio.to_thread(files.read_bytes_checked, ctx.jail, a.path)
    except PathRejected as e:
        return Prepared(error=f"refused: {e.reason}")
    except files.NotReadable as e:
        return Prepared(error=f"cannot edit {a.path}: {e.reason}")
    raw = data.decode("utf-8", errors="strict") if _is_utf8(data) else None
    if raw is None:
        return Prepared(error=f"{a.path} is not valid UTF-8 text")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    old = a.old_text.replace("\r\n", "\n")
    new = a.new_text.replace("\r\n", "\n")
    if not old:
        return Prepared(error="old_text is empty")
    if old == new:
        return Prepared(error="old_text and new_text are identical")
    n = text.count(old)
    if n == 0:
        return Prepared(
            error=(
                "old_text was not found. Re-read the file: indentation and whitespace must match "
                "exactly."
            )
        )
    if n > 1:
        return Prepared(
            error=f"old_text occurs {n} times; include more surrounding lines so it is unique"
        )
    updated = text.replace(old, new, 1)
    diff = _udiff(rel, text, updated)
    out = updated.replace("\n", "\r\n") if crlf else updated
    return Prepared(preview=diff, state=(ctx.jail.root / rel, rel, out.encode("utf-8"), diff))


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


async def edit_file(ctx: ToolContext, a: EditIn, prepared: Prepared | None) -> ToolOutcome:
    p = await prepare_edit(ctx, a)  # re-validate against the file as it is now
    if p.error:
        return ToolOutcome(p.error, ok=False)
    target, rel, new_bytes, diff = p.state
    await asyncio.to_thread(Path(target).write_bytes, new_bytes)
    added, removed = _count(diff)
    return ToolOutcome(
        f"edited {rel} (+{added} -{removed})\n{diff}",
        evidence=EvidenceDraft(
            "edit",
            f"edit {rel} (+{added} -{removed}): {a.reason}"[:200],
            diff,
            {"path": rel, "added": added, "removed": removed, "reason": a.reason, "op": "edit"},
        ),
    )


class CreateIn(BaseModel):
    path: str = Field(description="Repository-relative path of a NEW file (e.g. a regression test)")
    content: str
    reason: str = Field(description="One sentence: why this file")


async def prepare_create(ctx: ToolContext, a: CreateIn) -> Prepared:
    if ctx.workspace_path is None:
        return Prepared(error="this task cannot modify files")
    try:
        real, rel = ctx.jail.resolve(a.path)
    except PathRejected as e:
        return Prepared(error=f"refused: {e.reason}")
    if real.exists():
        return Prepared(error=f"{rel} already exists; use edit_file to change it")
    data = a.content.encode("utf-8")
    if len(data) > MAX_NEW_FILE:
        return Prepared(error=f"content is too large ({len(data)} bytes; limit {MAX_NEW_FILE})")
    diff = _udiff(rel, "", a.content)
    return Prepared(preview=diff, state=(real, rel, data, diff))


async def create_file(ctx: ToolContext, a: CreateIn, prepared: Prepared | None) -> ToolOutcome:
    p = await prepare_create(ctx, a)
    if p.error:
        return ToolOutcome(p.error, ok=False)
    real, rel, data, diff = p.state

    def _write() -> None:
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(data)

    await asyncio.to_thread(_write)
    added, _ = _count(diff)
    return ToolOutcome(
        f"created {rel} ({added} lines)",
        evidence=EvidenceDraft(
            "edit",
            f"create {rel}: {a.reason}"[:200],
            diff,
            {"path": rel, "added": added, "removed": 0, "reason": a.reason, "op": "create"},
        ),
    )


class RevertIn(BaseModel):
    path: str = Field(
        description="Repository-relative path whose changes in this task should be undone"
    )


async def prepare_revert(ctx: ToolContext, a: RevertIn) -> Prepared:
    if ctx.workspace_path is None or ctx.baseline is None:
        return Prepared(error="this task has no workspace")
    try:
        _real, rel = ctx.jail.resolve(a.path)
    except PathRejected as e:
        return Prepared(error=f"refused: {e.reason}")
    changed = await asyncio.to_thread(workspace.changed_files, ctx.workspace_path, ctx.baseline)
    if rel not in changed:
        return Prepared(error=f"{rel} has no changes to revert")
    return Prepared(preview=f"undo all changes to {rel}", state=rel)


async def revert_file(ctx: ToolContext, a: RevertIn, prepared: Prepared | None) -> ToolOutcome:
    p = await prepare_revert(ctx, a)
    if p.error:
        return ToolOutcome(p.error, ok=False)
    rel = p.state
    assert ctx.workspace_path is not None and ctx.baseline is not None  # noqa: S101 - checked in prepare
    result = await asyncio.to_thread(workspace.revert, ctx.workspace_path, ctx.baseline, rel)
    return ToolOutcome(
        f"{rel}: {result}",
        evidence=EvidenceDraft(
            "edit", f"revert {rel} ({result})", f"reverted {rel}", {"path": rel, "op": "revert"}
        ),
    )


EDIT_TOOLS = [
    Tool(
        "edit_file",
        "Replace one exact, unique piece of text in an existing file in your workspace. Requires "
        "approval unless the policy allows edits.",
        EditIn,
        "write",
        edit_file,
        prepare_edit,
    ),
    Tool(
        "create_file",
        "Create a new file in your workspace (for example a regression test).",
        CreateIn,
        "write",
        create_file,
        prepare_create,
    ),
    Tool(
        "revert_file",
        "Undo every change you made to one file in this task.",
        RevertIn,
        "write",
        revert_file,
        prepare_revert,
    ),
]

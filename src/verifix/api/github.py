"""
GitHub API (M8): import a repository by URL, open a draft PR from a task, read CI checks.

Both write actions require `confirm: true`:
- import clones over the network and writes to disk;
- pull-request pushes a branch to the remote and opens a PR visible to others.
Each is recorded on the task timeline / as an approval row, like `apply`.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from verifix.agent import workspace
from verifix.agent.store import now
from verifix.api.repos import RepoOut
from verifix.core.config import Settings, get_settings
from verifix.core.db import get_session
from verifix.github import ops
from verifix.github.client import GitHubClient, GitHubError, parse_repo_url
from verifix.models import Approval, Repo, Step, Task
from verifix.repos import registry

router = APIRouter(tags=["github"])
Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _token(settings: Settings) -> str | None:
    return settings.github_token.get_secret_value() if settings.github_token else None


def _client(request: Request, settings: Settings) -> GitHubClient:
    return GitHubClient(
        _token(settings),
        settings.github_api_url,
        transport=getattr(request.app.state, "github_transport", None),
    )


@router.get("/github/status")
async def status(settings: SettingsDep) -> dict[str, Any]:
    return {"token_configured": settings.github_token is not None, "api": settings.github_api_url}


class ImportIn(BaseModel):
    url: str = Field(max_length=300)
    confirm: bool = Field(False, description="Must be true: clones over the network to disk")


@router.post("/github/import", status_code=201)
async def import_repo(body: ImportIn, session: Session, settings: SettingsDep) -> dict[str, Any]:
    try:
        ref = parse_repo_url(body.url)
    except GitHubError as e:
        raise HTTPException(422, str(e)) from None
    dest = f"{settings.clones_dir.rstrip('/\\')}/{ref.owner}__{ref.name}"
    if not body.confirm:
        raise HTTPException(
            422, f"importing clones {ref.https_url} into {dest}; resend with confirm=true"
        )
    try:
        path, cloned = await asyncio.to_thread(
            ops.clone, ref, Path(settings.clones_dir), _token(settings)
        )
    except GitHubError as e:
        raise HTTPException(502, str(e)) from None
    try:
        repo, created = await registry.register(session, str(path), settings)
    except registry.RegistrationError as e:
        raise HTTPException(
            409,
            f"cloned to {path} but could not register it: {e}. "
            "Add the clones directory to VERIFIX_ALLOWED_ROOTS.",
        ) from None
    return {"repo": RepoOut.of(repo).model_dump(mode="json"), "cloned": cloned, "created": created}


class PullRequestIn(BaseModel):
    confirm: bool = Field(False, description="Must be true: pushes a branch and opens a PR")
    title: str | None = Field(None, max_length=200)
    base: str | None = Field(None, max_length=200, description="Default: the repo's default branch")
    draft: bool = True


def _pr_body(t: Task, stats: dict[str, int]) -> str:
    r = t.report or {}
    lines = [
        f"**verifix task** `{str(t.id)[:8]}` — status **{t.status}**",
        "",
        f"> {t.request.strip()[:800]}",
        "",
        "### Summary",
        str(r.get("summary", "")).strip() or "_no summary_",
    ]
    claims = r.get("claims") or []
    if claims:
        lines += ["", "### Claims (with evidence ids from the task's ledger)"]
        for c in claims[:20]:
            ev = ", ".join(f"E{n}" for n in c.get("evidence", []))
            mark = "" if c.get("supported", True) else " ⚠️ unsupported"
            lines.append(f"- {c.get('statement', '')} [{ev}]{mark}")
    ver = r.get("verification") or {}
    if ver:
        lines += [
            "",
            "### Verification",
            f"- reproduced before fix: {ver.get('reproduced_before_fix')}",
            "- last test run after the last edit: "
            + str((ver.get("last_run_after_last_change") or {}).get("status", "none")),
        ]
    if t.status != "verified":
        lines += ["", "⚠️ **Not verified**: no passing test run after the final edit."]
    if r.get("uncertainty"):
        lines += ["", "### Uncertainty", str(r["uncertainty"])]
    lines += [
        "",
        f"_{stats.get('files', 0)} files, +{stats.get('added', 0)} -{stats.get('removed', 0)}. "
        "Opened as a draft by verifix: review before merging._",
    ]
    return "\n".join(lines)


async def _add_step(session: AsyncSession, task_id: uuid.UUID, summary: str, ok: bool) -> None:
    seq = (
        await session.execute(
            select(func.coalesce(func.max(Step.seq), 0)).where(Step.task_id == task_id)
        )
    ).scalar_one()
    session.add(
        Step(
            task_id=task_id,
            seq=int(seq) + 1,
            kind="approval",
            name="pull_request",
            summary=summary[:500],
            ok=ok,
        )
    )


@router.post("/tasks/{task_id}/pull-request")
async def open_pull_request(
    task_id: uuid.UUID,
    body: PullRequestIn,
    request: Request,
    session: Session,
    settings: SettingsDep,
) -> dict[str, Any]:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    if t.status not in ("verified", "fixed_unverified"):
        raise HTTPException(409, f"task is {t.status}; only finished fix tasks can become a PR")
    if (t.report or {}).get("pull_request"):
        raise HTTPException(409, "a pull request was already opened for this task")
    if not t.worktree_path or not t.baseline_commit or not t.source_head:
        raise HTTPException(404, "this task has no workspace changes")
    if settings.github_token is None:
        raise HTTPException(400, "set VERIFIX_GITHUB_TOKEN to open pull requests")
    repo = await session.get(Repo, t.repo_id)
    assert repo is not None  # noqa: S101 - FK
    source = Path(repo.root_path)
    ref = await asyncio.to_thread(ops.origin, source)
    if ref is None:
        raise HTTPException(409, "the repository's origin remote is not a github.com repository")
    ws = Path(t.worktree_path)
    if not await asyncio.to_thread(ws.is_dir):
        raise HTTPException(410, "the workspace for this task has been removed")
    patch = await asyncio.to_thread(workspace.diff, ws, t.baseline_commit, binary=True)
    if not patch.strip():
        raise HTTPException(409, "the task made no changes")
    stats = await asyncio.to_thread(workspace.diff_stats, ws, t.baseline_commit)
    title = body.title or f"verifix: {t.title}"[:200]
    if not body.confirm:
        raise HTTPException(
            422,
            f"this pushes branch verifix/pr-{str(t.id)[:12]} to {ref.full_name} and opens a "
            f"{'draft ' if body.draft else ''}PR '{title}'; resend with confirm=true",
        )

    client = _client(request, settings)
    branch = f"verifix/pr-{str(t.id)[:12]}"
    try:
        info = await client.get_repo(ref)
        base = body.base or info.get("default_branch") or "main"
        sha = await asyncio.to_thread(
            ops.build_branch, source, t.source_head, patch.encode("utf-8"), branch, title
        )
        await asyncio.to_thread(ops.push, source, branch, _token(settings))
        pr = await client.create_pull(
            ref, head=branch, base=base, title=title, body=_pr_body(t, stats), draft=body.draft
        )
    except GitHubError as e:
        await _add_step(session, t.id, f"pull request failed: {e}", ok=False)
        await session.commit()
        raise HTTPException(502, str(e)) from None
    finally:
        await client.aclose()

    result = {
        "url": pr.get("html_url"),
        "number": pr.get("number"),
        "branch": branch,
        "commit": sha,
        "base": base,
        "repo": ref.full_name,
        "draft": body.draft,
    }
    t.report = {**(t.report or {}), "pull_request": result}
    session.add(
        Approval(
            task_id=t.id,
            action="pull_request",
            payload=result,
            preview=title,
            status="approved",
            reason="opened by the user",
            decided_at=now(),
        )
    )
    await _add_step(session, t.id, f"opened PR #{result['number']}: {result['url']}", ok=True)
    await session.commit()
    return result


@router.get("/tasks/{task_id}/pull-request/checks")
async def pull_request_checks(
    task_id: uuid.UUID, request: Request, session: Session, settings: SettingsDep
) -> dict[str, Any]:
    t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    pr = (t.report or {}).get("pull_request")
    if not pr:
        raise HTTPException(404, "no pull request for this task")
    owner, name = pr["repo"].split("/", 1)
    from verifix.github.client import RepoRef

    client = _client(request, settings)
    try:
        checks = await client.check_runs(RepoRef(owner, name), pr["commit"])
    except GitHubError as e:
        raise HTTPException(502, str(e)) from None
    finally:
        await client.aclose()
    concl = {c["conclusion"] for c in checks}
    overall = (
        "none"
        if not checks
        else "pending"
        if any(c["status"] != "completed" for c in checks)
        else "failure"
        if concl & {"failure", "timed_out", "cancelled", "action_required"}
        else "success"
    )
    return {"pull_request": pr, "overall": overall, "checks": checks}

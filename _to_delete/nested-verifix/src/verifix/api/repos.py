"""
Repository explorer API (M1). Everything here is read-only.

All filesystem and git work runs in a worker thread (asyncio.to_thread) so a slow git
command never blocks the event loop.
"""

import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from verifix.core.config import Settings, get_settings
from verifix.core.db import get_session
from verifix.models import Repo
from verifix.repos import detect, files, gitinfo, registry, search, symbols
from verifix.repos.gitcli import GitError, UnsafeRepoConfig
from verifix.repos.paths import PathRejected

router = APIRouter(prefix="/repos", tags=["repos"])

Session = Annotated[AsyncSession, Depends(get_session)]


class RegisterIn(BaseModel):
    path: str


class RepoOut(BaseModel):
    id: uuid.UUID
    name: str
    root_path: str
    default_branch: str | None
    created_at: datetime

    @classmethod
    def of(cls, r: Repo) -> "RepoOut":
        return cls(
            id=r.id,
            name=r.name,
            root_path=r.root_path,
            default_branch=r.default_branch,
            created_at=r.created_at,
        )


async def _jail(session: AsyncSession, repo_id: uuid.UUID):  # type: ignore[no-untyped-def]
    try:
        repo = await registry.get_repo(session, repo_id)
        return await asyncio.to_thread(registry.jail_for, repo)
    except registry.RepoNotFound as e:
        raise HTTPException(404, f"repository not found: {e}") from None
    except UnsafeRepoConfig as e:
        raise HTTPException(409, str(e)) from None


def _bad(e: Exception) -> HTTPException:
    if isinstance(e, PathRejected):
        return HTTPException(403, f"path rejected: {e.reason}")
    if isinstance(e, files.NotReadable):
        return HTTPException(422, f"cannot read: {e.reason}")
    if isinstance(e, search.BadPattern):
        return HTTPException(422, str(e))
    if isinstance(e, GitError | ValueError):
        return HTTPException(422, str(e))
    raise e


@router.post("", status_code=201, response_model=RepoOut)
async def register_repo(
    body: RegisterIn, session: Session, settings: Annotated[Settings, Depends(get_settings)]
) -> RepoOut:
    try:
        repo, _created = await registry.register(session, body.path, settings)
    except registry.RegistrationError as e:
        raise HTTPException(422, str(e)) from None
    return RepoOut.of(repo)


@router.get("", response_model=list[RepoOut])
async def list_repos(session: Session) -> list[RepoOut]:
    rows = (await session.execute(select(Repo).order_by(Repo.created_at.desc()))).scalars()
    return [RepoOut.of(r) for r in rows]


@router.get("/{repo_id}", response_model=RepoOut)
async def get_repo(repo_id: uuid.UUID, session: Session) -> RepoOut:
    try:
        return RepoOut.of(await registry.get_repo(session, repo_id))
    except registry.RepoNotFound:
        raise HTTPException(404, "repository not found") from None


@router.delete("/{repo_id}", status_code=204)
async def unregister_repo(repo_id: uuid.UUID, session: Session) -> None:
    """Forget a repository (and its tasks). Never touches the files on disk."""
    repo = await session.get(Repo, repo_id)
    if repo is None:
        raise HTTPException(404, "repository not found")
    await session.delete(repo)
    await session.commit()


@router.get("/{repo_id}/overview")
async def repo_overview(repo_id: uuid.UUID, session: Session) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    return asdict(await asyncio.to_thread(detect.overview, jail))


@router.get("/{repo_id}/files")
async def repo_files(repo_id: uuid.UUID, session: Session) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    return asdict(await asyncio.to_thread(files.list_files, jail))


@router.get("/{repo_id}/file")
async def repo_file(
    repo_id: uuid.UUID,
    session: Session,
    path: str,
    start: Annotated[int, Query(ge=1)] = 1,
    end: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    try:
        return asdict(await asyncio.to_thread(files.read_file, jail, path, start, end))
    except (PathRejected, files.NotReadable) as e:
        raise _bad(e) from None


@router.get("/{repo_id}/search")
async def repo_search(
    repo_id: uuid.UUID,
    session: Session,
    q: str,
    regex: bool = False,
    case_sensitive: bool = False,
    glob: str | None = None,
) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    try:
        result = await asyncio.to_thread(
            search.search, jail, q, regex=regex, case_sensitive=case_sensitive, path_glob=glob
        )
    except search.BadPattern as e:
        raise _bad(e) from None
    return asdict(result)


@router.get("/{repo_id}/symbols")
async def repo_symbols(
    repo_id: uuid.UUID, session: Session, name: str | None = None, prefix: str | None = None
) -> list[dict[str, Any]]:
    jail = await _jail(session, repo_id)
    if name:
        found = await asyncio.to_thread(symbols.find_symbol, jail, name)
    else:
        found = await asyncio.to_thread(symbols.index_symbols, jail, path_prefix=prefix)
    return [asdict(s) for s in found]


@router.get("/{repo_id}/git/status")
async def repo_git_status(repo_id: uuid.UUID, session: Session) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    st = await asyncio.to_thread(gitinfo.status, jail)
    return {**asdict(st), "dirty": st.dirty}


@router.get("/{repo_id}/git/diff")
async def repo_git_diff(
    repo_id: uuid.UUID, session: Session, staged: bool = False, base: str | None = None
) -> dict[str, Any]:
    jail = await _jail(session, repo_id)
    try:
        return asdict(await asyncio.to_thread(gitinfo.diff, jail, base=base, staged=staged))
    except (GitError, ValueError) as e:
        raise _bad(e) from None

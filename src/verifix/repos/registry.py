"""Registering repositories and looking them up. The only module that maps a repo id to a path."""

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from verifix.core.config import Settings
from verifix.models import Repo
from verifix.repos.gitcli import GitError, UnsafeRepoConfig, current_branch, ensure_safe, toplevel
from verifix.repos.paths import RepoJail


class RegistrationError(ValueError):
    pass


class RepoNotFound(LookupError):
    pass


def _check_allowed(path: Path, settings: Settings) -> None:
    roots = settings.allowed_root_list
    if not roots:
        return
    for root in roots:
        try:
            if path.is_relative_to(root.resolve()):
                return
        except OSError:
            continue
    raise RegistrationError(
        "path is outside VERIFIX_ALLOWED_ROOTS (" + ", ".join(str(r) for r in roots) + ")"
    )


def inspect_path(raw: str, settings: Settings) -> tuple[Path, str | None]:
    """Validate a user-supplied path. Returns (repo toplevel, current branch)."""
    if not raw or not raw.strip():
        raise RegistrationError("path is required")
    try:
        path = Path(raw.strip()).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise RegistrationError("path does not exist") from None
    if not path.is_dir():
        raise RegistrationError("path is not a directory")
    _check_allowed(path, settings)
    try:
        top = toplevel(path)
    except GitError:
        raise RegistrationError("not a git repository (git rev-parse failed)") from None
    _check_allowed(top, settings)
    try:
        ensure_safe(top)
    except UnsafeRepoConfig as e:
        raise RegistrationError(str(e)) from None
    return top, current_branch(top)


async def register(session: AsyncSession, raw_path: str, settings: Settings) -> tuple[Repo, bool]:
    """Register a repository. Idempotent: returns (repo, created)."""
    top, branch = await asyncio.to_thread(inspect_path, raw_path, settings)
    existing = (
        await session.execute(select(Repo).where(Repo.root_path == str(top)))
    ).scalar_one_or_none()
    if existing:
        return existing, False
    repo = Repo(name=top.name, root_path=str(top), default_branch=branch)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return repo, True


async def get_repo(session: AsyncSession, repo_id: uuid.UUID) -> Repo:
    repo = await session.get(Repo, repo_id)
    if repo is None:
        raise RepoNotFound(str(repo_id))
    return repo


def jail_for(repo: Repo) -> RepoJail:
    """A jail on the registered checkout. Re-checks the config every time: it can change."""
    root = Path(repo.root_path)
    if not root.is_dir():
        raise RepoNotFound(f"{repo.root_path} no longer exists")
    ensure_safe(root)
    return RepoJail(root)

"""
Migration and model round-trip against a real Postgres (the docker-compose one).

Runs `alembic upgrade head` on the separate verifix_test database, writes a repo, task
and evidence row, checks the constraints that encode our rules, then downgrades to base.
Proves the migration and the ORM models agree. Skips cleanly when Postgres is down.
"""

import asyncio
import hashlib
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from verifix.models import Base, Evidence, Repo, Task

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[1]


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging"] = True
    return cfg


async def _drop_everything(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DROP TABLE IF EXISTS steps, approvals, findings, eval_runs, evidence, tasks, repos, alembic_version CASCADE"
            )
        )
    await engine.dispose()


async def _roundtrip(url: str) -> None:
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        repo = Repo(name="demo", root_path=f"/tmp/demo-{uuid.uuid4()}")
        s.add(repo)
        await s.flush()
        task = Task(repo_id=repo.id, kind="investigate", request="why 500 on missing email?")
        s.add(task)
        await s.flush()
        task_id = task.id  # keep the plain value: a rollback below expires the ORM object
        content = "def login(email): ..."
        s.add(
            Evidence(
                task_id=task_id,
                seq=1,
                kind="file_read",
                summary="read app/auth.py:1-1",
                content=content,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                meta={"path": "app/auth.py", "start": 1, "end": 1},
            )
        )
        await s.commit()

        got = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert got.status == "queued"
        assert got.model_calls == 0

        # The database itself refuses statuses outside the vocabulary.
        got.status = "probably_fixed"
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()

        # ...and evidence without a proper SHA-256.
        s.add(
            Evidence(
                task_id=task_id,
                seq=2,
                kind="note",
                summary="x",
                content="x",
                content_sha256="short",
                meta={},
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()

        # ...and two evidence rows with the same sequence number in one task.
        s.add(
            Evidence(
                task_id=task_id,
                seq=1,
                kind="note",
                summary="dup",
                content="x",
                content_sha256=hashlib.sha256(b"x").hexdigest(),
                meta={},
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
    await engine.dispose()


def test_migration_matches_models_and_enforces_rules(test_db_url: str) -> None:
    asyncio.run(_drop_everything(test_db_url))
    cfg = _alembic(test_db_url)
    command.upgrade(cfg, "head")
    try:
        asyncio.run(_roundtrip(test_db_url))
    finally:
        command.downgrade(cfg, "base")
    asyncio.run(_drop_everything(test_db_url))


def test_models_and_migration_declare_the_same_tables() -> None:
    assert set(Base.metadata.tables) == {
        "repos",
        "tasks",
        "evidence",
        "steps",
        "approvals",
        "findings",
        "eval_runs",
    }


async def _schema_drift(url: str) -> list:
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    engine = create_async_engine(url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )
    await engine.dispose()
    return diff


def test_migrations_produce_exactly_the_model_schema(test_db_url: str) -> None:
    """Validate the ruler: the migrated database and the ORM models must not drift."""
    asyncio.run(_drop_everything(test_db_url))
    cfg = _alembic(test_db_url)
    command.upgrade(cfg, "head")
    try:
        assert asyncio.run(_schema_drift(test_db_url)) == []
    finally:
        command.downgrade(cfg, "base")
        asyncio.run(_drop_everything(test_db_url))

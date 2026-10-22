"""
Shared test setup.

1. No network. Any attempt to open a socket to a non-loopback address fails the test.
   This is what makes "the test suite never calls the real Anthropic API" a checked fact
   rather than a hope. Loopback stays open for the docker-compose Postgres.
2. A clean environment. Real API keys from the developer's shell or .env never leak into
   a test: each test gets settings built only from what it sets.
3. The `db` marker: tests that need Postgres skip with a clear reason when it is down,
   instead of hanging or failing with a connection traceback.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Iterator

import pytest

from verifix.core.config import Settings, get_settings

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class NetworkBlocked(RuntimeError):
    pass


def _is_loopback(address: object) -> bool:
    if isinstance(address, tuple) and address:
        host = str(address[0])
        if host in ("localhost", ""):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return True  # AF_UNIX paths and other non-IP sockets


def _guarded_connect(self: socket.socket, address: object) -> None:
    if not _is_loopback(address):
        raise NetworkBlocked(f"test tried to open a network connection to {address!r}")
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: object) -> int:
    if not _is_loopback(address):
        raise NetworkBlocked(f"test tried to open a network connection to {address!r}")
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)


@pytest.fixture(autouse=True)
def _clean_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    for name in ("ANTHROPIC_API_KEY", "VERIFIX_ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERIFIX_ENV", "test")
    # Run from an empty directory so a developer's .env is never read by Settings().
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def _test_db_url() -> str:
    import os

    return os.environ.get("VERIFIX_TEST_DATABASE_URL") or Settings(_env_file=None).test_database_url


async def _db_reachable(url: str) -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture
def test_db_url() -> str:
    import concurrent.futures

    url = _test_db_url()
    # Probe in a separate thread: this fixture may be set up while an event loop is running.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        reachable = pool.submit(asyncio.run, _db_reachable(url)).result()
    if not reachable:
        pytest.skip(
            "test Postgres not reachable - start it with `docker compose up -d` "
            f"(looked for {url.split('@')[-1]})"
        )
    return url


# ------------------------------------------------------------------ database-backed API


@pytest.fixture
async def db_sessionmaker(test_db_url: str):  # type: ignore[no-untyped-def]
    """Fresh schema in the test database for one test, created from the models."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from verifix.models import Base

    engine = create_async_engine(test_db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def api(db_sessionmaker, settings: Settings):  # type: ignore[no-untyped-def]
    """An httpx client on the real app, with sessions bound to the test database."""
    import httpx

    from verifix.core.db import get_session
    from verifix.main import create_app

    app = create_app(settings)

    async def _session():  # type: ignore[no-untyped-def]
        async with db_sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.state.sessionmaker = db_sessionmaker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.app = app  # type: ignore[attr-defined]
        yield client

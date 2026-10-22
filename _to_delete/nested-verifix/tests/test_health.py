import httpx
import pytest

from verifix.api.health import get_db_check
from verifix.core.config import Settings
from verifix.main import create_app

SECRET = "sk-ant-api03-HealthEndpointMustNotLeak01"


def _client(settings: Settings, db_ok: bool) -> httpx.AsyncClient:
    app = create_app(settings)

    async def fake_check() -> bool:
        return db_ok

    app.dependency_overrides[get_db_check] = lambda: fake_check
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_ok_when_db_up_and_key_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    async with _client(Settings(_env_file=None), db_ok=True) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["anthropic"] == "configured"
    assert SECRET not in r.text
    assert "sk-ant" not in r.text  # not even the prefix


async def test_degraded_when_db_down() -> None:
    async with _client(Settings(_env_file=None), db_ok=False) as c:
        body = (await c.get("/health")).json()
    assert body["status"] == "degraded"
    assert body["database"] == "down"
    assert body["anthropic"] == "missing"


async def test_cors_allows_only_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERIFIX_CORS_ORIGINS", "http://localhost:5173")
    async with _client(Settings(_env_file=None), db_ok=True) as c:
        good = await c.get("/health", headers={"Origin": "http://localhost:5173"})
        evil = await c.get("/health", headers={"Origin": "https://evil.example"})
    assert good.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in evil.headers


def test_network_guard_blocks_real_hosts() -> None:
    import socket

    from tests.conftest import NetworkBlocked

    s = socket.socket()
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("93.184.216.34", 443))
    finally:
        s.close()

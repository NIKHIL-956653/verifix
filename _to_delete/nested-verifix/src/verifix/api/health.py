"""
GET /health - is the service usable?

Reports whether the database answers and whether a model key is configured. It reports
*whether* a key is configured, never the key, its length, or its prefix.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from verifix import __version__
from verifix.core.config import Settings, get_settings
from verifix.core.db import check_database

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    database: Literal["ok", "down"]
    anthropic: Literal["configured", "missing"]
    model_main: str


def get_db_check() -> Callable[[], Awaitable[bool]]:
    """Dependency so tests can swap the real database probe for a stub."""
    return check_database


@router.get("/health", response_model=Health)
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
    db_check: Annotated[Callable[[], Awaitable[bool]], Depends(get_db_check)],
) -> Health:
    db_ok = await db_check()
    configured = settings.anthropic_configured
    return Health(
        status="ok" if db_ok and configured else "degraded",
        version=__version__,
        env=settings.env,
        database="ok" if db_ok else "down",
        anthropic="configured" if configured else "missing",
        model_main=settings.model_main,
    )

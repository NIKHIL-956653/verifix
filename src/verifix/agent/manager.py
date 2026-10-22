"""
Runs agent tasks in the background of the API process (ADR-001: no queue service).

One asyncio task per running agent task. State lives in Postgres, not here, so this
object can be rebuilt at any time; on startup, tasks left "running" by a previous process
are marked failed (they can be retried - their conversation is saved).
"""

import asyncio
import uuid
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy import update

from verifix.agent.loop import AgentLoop
from verifix.agent.store import TaskStore, now
from verifix.core.logging import get_logger
from verifix.models import TERMINAL_STATUSES, Task

log = get_logger("verifix.manager")


class TaskManager:
    def __init__(self, store: TaskStore, loop_factory: Callable[[], AgentLoop]) -> None:
        self.store = store
        self.loop_factory = loop_factory
        self._running: dict[uuid.UUID, asyncio.Task[str]] = {}

    def is_running(self, task_id: uuid.UUID) -> bool:
        t = self._running.get(task_id)
        return t is not None and not t.done()

    def start(self, task_id: uuid.UUID) -> None:
        if self.is_running(task_id):
            return
        self._running[task_id] = asyncio.create_task(self._run(task_id), name=f"verifix-{task_id}")

    async def _run(self, task_id: uuid.UUID) -> str:
        try:
            return await self.loop_factory().run(task_id)
        except asyncio.CancelledError:
            return "cancelled"
        except Exception:
            log.exception("task runner crashed", task_id=str(task_id))
            return "failed"
        finally:
            self._running.pop(task_id, None)

    async def wait(self, task_id: uuid.UUID, limit_s: float | None = None) -> None:
        t = self._running.get(task_id)
        if t is not None:
            await asyncio.wait_for(asyncio.shield(t), limit_s)

    async def cancel(self, task_id: uuid.UUID) -> None:
        await self.store.update(task_id, cancel_requested=True)
        t = self._running.get(task_id)
        if t is not None and not t.done():
            t.cancel()
            with suppress(asyncio.CancelledError, Exception):  # the loop records the outcome
                await t
            return
        task = await self.store.get(task_id)
        if task.status not in TERMINAL_STATUSES:  # paused or queued: nothing is running
            await self.store.update(
                task_id,
                status="cancelled",
                error="cancelled by the user",
                finished_at=now(),
                pending=None,
            )
            await self.store.add_step(
                task_id, kind="status", summary="cancelled: cancelled by the user", ok=True
            )

    async def recover_interrupted(self) -> int:
        """Mark tasks a previous process left mid-run as failed (retryable)."""
        async with self.store.sm() as s:
            res = await s.execute(
                update(Task)
                .where(Task.status.in_(("running", "planning", "queued")))
                .values(
                    status="failed",
                    error="interrupted by a server restart; retry to continue",
                    finished_at=now(),
                )
            )
            await s.commit()
            return res.rowcount or 0

    async def shutdown(self) -> None:
        for t in list(self._running.values()):
            t.cancel()
        for t in list(self._running.values()):
            with suppress(asyncio.CancelledError, Exception):
                await t

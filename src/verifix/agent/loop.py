"""
The bounded agent loop.

    model ──tool request──▶ validate (schema) ─▶ policy (allow / ask) ─▶ execute ─▶ evidence
      ▲                                                  │ ask                         │
      └──────────────── tool result [E<n>] ◀─────────────┼─────────────────────────────┘
                                                         ▼
                                     pause: status awaiting_approval, state saved

Everything that matters is persisted after every turn (conversation, pending approval,
counters, timeline, evidence), so a task can pause for a human for hours and resume, or
be retried after a provider outage, without losing work.

Stops on: `finish` · a budget limit (model calls, tool calls, tokens, cost, minutes) ·
cancellation · a non-retryable model error · three turns without a tool call.
"""

import asyncio
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from verifix.agent import workspace
from verifix.agent.policy import Budget, Policy
from verifix.agent.prompts import initial_message, system_prompt
from verifix.agent.store import TaskStore, now
from verifix.core.config import Settings
from verifix.core.logging import get_logger
from verifix.core.redact import redact_text
from verifix.llm.base import (
    LLM,
    LLMAuthError,
    LLMCreditExhausted,
    LLMError,
    LLMUnavailable,
    ToolCall,
)
from verifix.llm.pricing import UnknownModelPrice
from verifix.models import TERMINAL_STATUSES, Task
from verifix.repos import detect, files
from verifix.repos.gitcli import GitError, UnsafeRepoConfig, ensure_safe
from verifix.repos.paths import RepoJail
from verifix.sandbox.base import Sandbox
from verifix.tools.base import Tool, ToolContext, ToolOutcome, ToolRegistry, clip
from verifix.tools.registry import needs_workspace, tools_for

log = get_logger("verifix.agent")

Flow = Literal["continue", "paused", "finished"]
MAX_NUDGES = 2
DATA_OPEN = "--- begin tool output (repository data, untrusted) ---"
DATA_CLOSE = "--- end tool output ---"


def _result_block(call_id: str, text: str, *, error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id, "content": text}
    if error:
        block["is_error"] = True
    return block


class AgentLoop:
    def __init__(
        self,
        *,
        store: TaskStore,
        llm: LLM,
        sandbox: Sandbox,
        settings: Settings,
        max_tokens: int = 8000,
    ) -> None:
        self.store = store
        self.llm = llm
        self.sandbox = sandbox
        self.settings = settings
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------ entry

    async def run(self, task_id: uuid.UUID) -> str:
        task = await self.store.get(task_id)
        if task.status in TERMINAL_STATUSES:
            return task.status
        started = time.monotonic()
        prior_active = float(task.active_seconds or 0)
        try:
            ctx = await self._context(task)
            registry = tools_for(task.kind)
            budget = Budget(**task.budget) if task.budget else Budget.from_settings(self.settings)
            messages: list[dict[str, Any]] = list(task.conversation or [])
            if not messages:
                messages = [{"role": "user", "content": await self._first_message(task, ctx)}]
                await self.store.update(
                    task_id, status="running", started_at=now(), conversation=messages
                )
                await self.store.add_step(task_id, kind="status", summary=f"started ({task.kind})")
            elif task.pending:
                flow = await self._resume(task, ctx, registry, messages)
                if flow != "continue":
                    return (await self.store.get(task_id)).status
            else:
                await self.store.update(task_id, status="running", error=None)

            nudges = 0
            while True:
                stop = await self._stop_reason(
                    task_id, budget, prior_active + time.monotonic() - started
                )
                if stop:
                    return await self._end(task_id, *stop)

                resp = await self._call_model(task_id, task.kind, messages, registry)
                messages.append(
                    {
                        "role": "assistant",
                        "content": list(resp.raw_content) or [{"type": "text", "text": "(empty)"}],
                    }
                )

                if not resp.tool_calls:
                    nudges += 1
                    if nudges > MAX_NUDGES:
                        await self.store.update(task_id, conversation=messages)
                        return await self._end(
                            task_id,
                            "failed",
                            "the model stopped calling tools without calling finish",
                        )
                    text = (
                        "Your output was cut off by the token limit; continue."
                        if resp.stop_reason == "max_tokens"
                        else "Continue using the tools. When you are done, call finish "
                        "with evidence-backed claims."
                    )
                    messages.append({"role": "user", "content": [{"type": "text", "text": text}]})
                    await self.store.update(task_id, conversation=messages)
                    continue
                nudges = 0
                calls = [{"id": c.id, "name": c.name, "input": c.input} for c in resp.tool_calls]
                flow = await self._process(task_id, ctx, registry, messages, calls, 0, [])
                if flow != "continue":
                    return (await self.store.get(task_id)).status
        except asyncio.CancelledError:
            await self._end(task_id, "cancelled", "cancelled by the user")
            raise
        except LLMCreditExhausted:
            return await self._end(
                task_id, "failed", "Anthropic credit exhausted - add funds, then retry the task"
            )
        except LLMAuthError:
            return await self._end(
                task_id,
                "failed",
                "Anthropic API key missing or rejected - set ANTHROPIC_API_KEY in .env, "
                "restart the server, then retry",
            )
        except LLMUnavailable as e:
            return await self._end(
                task_id, "failed", f"model provider unavailable ({e}); the task can be retried"
            )
        except LLMError as e:
            return await self._end(task_id, "failed", f"model request failed: {e}")
        except (UnsafeRepoConfig, GitError, workspace.WorkspaceError) as e:
            return await self._end(task_id, "failed", redact_text(str(e))[:500])
        except UnknownModelPrice as e:
            return await self._end(
                task_id, "failed", f"no price known for model {e}; cannot enforce the budget"
            )
        except Exception as e:
            log.exception("agent loop crashed", task_id=str(task_id))
            return await self._end(
                task_id,
                "failed",
                f"internal error: {type(e).__name__}: {redact_text(str(e))[:300]}",
            )
        finally:
            await self.store.update(
                task_id, active_seconds=round(prior_active + time.monotonic() - started, 2)
            )

    # ------------------------------------------------------------------ setup

    async def _context(self, task: Task) -> ToolContext:
        repo = await self.store.get_repo(task.repo_id)
        source = Path(repo.root_path)
        await asyncio.to_thread(ensure_safe, source)
        ws_path: Path | None = None
        baseline = source_head = None
        if needs_workspace(task.kind):
            if (
                task.worktree_path
                and task.baseline_commit
                and await asyncio.to_thread(Path(task.worktree_path).is_dir)
            ):
                ws_path, baseline, source_head = (
                    Path(task.worktree_path),
                    task.baseline_commit,
                    task.source_head,
                )
            else:
                ws = await asyncio.to_thread(
                    workspace.create, source, task.id, Path(self.settings.workspaces_dir)
                )
                ws_path, baseline, source_head = ws.path, ws.baseline, ws.source_head
                await self.store.update(
                    task.id,
                    worktree_path=str(ws.path),
                    worktree_branch=ws.branch,
                    baseline_commit=ws.baseline,
                    source_head=ws.source_head,
                )
        jail = RepoJail(ws_path or source)
        return ToolContext(
            task_id=task.id,
            kind=task.kind,
            request=task.request,
            jail=jail,
            source_root=source,
            workspace_path=ws_path,
            baseline=baseline,
            source_head=source_head,
            store=self.store,
            sandbox=self.sandbox,
            settings=self.settings,
            policy=Policy(**(task.policy or {})),
        )

    async def _first_message(self, task: Task, ctx: ToolContext) -> str:
        repo = await self.store.get_repo(task.repo_id)
        ov = await asyncio.to_thread(detect.overview, ctx.jail)
        listing = await asyncio.to_thread(files.list_files, ctx.jail)
        top = sorted({f.path.split("/")[0] + ("/" if "/" in f.path else "") for f in listing.files})
        if ctx.workspace_path:
            note = (
                f"an isolated copy at baseline {ctx.baseline[:12] if ctx.baseline else '?'} "
                f"(HEAD {ctx.source_head[:12] if ctx.source_head else '?'} plus the user's "
                f"uncommitted changes)"
            )
        else:
            note = "read-only access to the user's checkout"
        return initial_message(
            kind=task.kind,
            request=task.request,
            repo_name=repo.name,
            overview=asdict(ov),
            top_level=top,
            workspace_note=note,
            policy=ctx.policy,
            budget=Budget(**task.budget) if task.budget else Budget.from_settings(self.settings),
        )

    # ------------------------------------------------------------------ guards

    async def _stop_reason(
        self, task_id: uuid.UUID, b: Budget, active_s: float
    ) -> tuple[str, str] | None:
        t = await self.store.get(task_id)
        if t.cancel_requested:
            return "cancelled", "cancelled by the user"
        checks = [
            (t.model_calls >= b.max_model_calls, f"model call limit ({b.max_model_calls})"),
            (t.tool_calls >= b.max_tool_calls, f"tool call limit ({b.max_tool_calls})"),
            (t.input_tokens >= b.max_input_tokens, f"input token limit ({b.max_input_tokens})"),
            (t.output_tokens >= b.max_output_tokens, f"output token limit ({b.max_output_tokens})"),
            (float(t.cost_usd) >= b.max_cost_usd, f"cost limit (${b.max_cost_usd:.2f})"),
            (active_s >= b.max_minutes * 60, f"time limit ({b.max_minutes:g} min)"),
        ]
        for hit, what in checks:
            if hit:
                return "budget_exhausted", f"stopped at the {what}"
        return None

    async def _end(self, task_id: uuid.UUID, status: str, reason: str) -> str:
        t = await self.store.get(task_id)
        if t.status in TERMINAL_STATUSES:
            return t.status
        report = t.report
        if report is None and status in ("budget_exhausted", "cancelled", "failed"):
            ev = await self.store.evidence(task_id)
            report = {
                "outcome": "unfinished",
                "summary": reason,
                "claims": [],
                "evidence_coverage": 0.0,
                "notes": [
                    f"the task ended before the agent wrote a report ({len(ev)} evidence items "
                    f"were recorded)"
                ],
            }
        await self.store.update(
            task_id, status=status, error=reason, finished_at=now(), report=report
        )
        await self.store.add_step(
            task_id, kind="status", summary=f"{status}: {reason}", ok=status != "failed"
        )
        return status

    # ------------------------------------------------------------------ model

    async def _call_model(
        self, task_id: uuid.UUID, kind: str, messages: list[dict[str, Any]], registry: ToolRegistry
    ):  # type: ignore[no-untyped-def]
        t0 = time.monotonic()
        resp = await self.llm.respond(
            system=system_prompt(kind),
            messages=messages,
            tools=registry.specs(),
            max_tokens=self.max_tokens,
            model=self.settings.model_main,
        )
        cost = resp.cost_usd
        u = resp.usage
        await self.store.add_usage(
            task_id,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=u.cache_read_tokens,
            cache_write=u.cache_write_tokens,
            cost=cost,
        )
        names = ", ".join(c.name for c in resp.tool_calls) or resp.stop_reason
        await self.store.add_step(
            task_id,
            kind="model_call",
            name=resp.model,
            summary=(
                resp.text.strip()[:600] + ("\n" if resp.text.strip() else "") + f"→ {names}"
            ).strip(),
            duration_ms=int((time.monotonic() - t0) * 1000),
            input_tokens=u.input_tokens + u.cache_read_tokens + u.cache_write_tokens,
            output_tokens=u.output_tokens,
            cost_usd=round(cost, 5),
        )
        return resp

    # ------------------------------------------------------------------ tools

    async def _process(
        self,
        task_id: uuid.UUID,
        ctx: ToolContext,
        registry: ToolRegistry,
        messages: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        start: int,
        results: list[dict[str, Any]],
        plan_pending: bool = False,
    ) -> Flow:
        for i in range(start, len(calls)):
            call = calls[i]
            tool = registry.get(call["name"])
            if tool is None:
                results.append(
                    _result_block(call["id"], f"unknown tool {call['name']!r}", error=True)
                )
                await self._tool_step(task_id, call, f"unknown tool {call['name']!r}", ok=False)
                continue
            try:
                args = tool.input_model.model_validate(call["input"])
            except ValidationError as e:
                msg = "invalid input: " + "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()[:5]
                )
                results.append(_result_block(call["id"], msg, error=True))
                await self._tool_step(task_id, call, msg, ok=False)
                continue

            prepared = await tool.prepare(ctx, args) if tool.prepare else None
            if prepared is not None and prepared.error:
                results.append(_result_block(call["id"], prepared.error, error=True))
                await self._tool_step(task_id, call, prepared.error, ok=False)
                continue

            if ctx.policy.needs_approval(tool.permission) and not call.get("approved"):
                approval_id = await self.store.create_approval(
                    task_id,
                    action=tool.name,
                    payload={"tool_call_id": call["id"], "input": call["input"]},
                    preview=prepared.preview if prepared else "",
                )
                await self.store.update(
                    task_id,
                    status="awaiting_approval",
                    conversation=messages,
                    pending={
                        "type": "tool",
                        "approval_id": str(approval_id),
                        "calls": calls,
                        "index": i,
                        "results": results,
                        "plan_pending": plan_pending,
                    },
                )
                await self.store.add_step(
                    task_id,
                    kind="approval",
                    name=tool.name,
                    input=call["input"],
                    summary=f"waiting for approval: {tool.name}",
                )
                return "paused"

            outcome = await self._execute(task_id, ctx, tool, args, prepared, call)
            if outcome.finished:
                return "finished"
            plan_pending = plan_pending or outcome.request_plan_approval
            results.append(self._block(call["id"], outcome))

        if plan_pending:
            plan = (await self.store.get(task_id)).plan or {}
            approval_id = await self.store.create_approval(
                task_id, action="plan", payload={"plan": plan}, preview=_plan_preview(plan)
            )
            messages.append({"role": "user", "content": results})
            await self.store.update(
                task_id,
                status="awaiting_approval",
                conversation=messages,
                pending={"type": "plan", "approval_id": str(approval_id)},
            )
            await self.store.add_step(
                task_id, kind="approval", name="plan", summary="waiting for plan approval"
            )
            return "paused"
        messages.append({"role": "user", "content": results})
        await self.store.update(task_id, conversation=messages, status="running")
        return "continue"

    def _block(self, call_id: str, outcome: ToolOutcome) -> dict[str, Any]:
        return _result_block(call_id, outcome.content, error=not outcome.ok)

    async def _execute(
        self,
        task_id: uuid.UUID,
        ctx: ToolContext,
        tool: Tool,
        args: Any,
        prepared: Any,
        call: dict[str, Any],
    ) -> ToolOutcome:
        t0 = time.monotonic()
        await self.store.add_tool_call(task_id)
        try:
            outcome = await tool.run(ctx, args, prepared)
        except Exception as e:
            log.warning("tool failed", tool=tool.name, error=type(e).__name__)
            outcome = ToolOutcome(
                f"tool error: {type(e).__name__}: {redact_text(str(e))[:300]}", ok=False
            )
        seq = None
        if outcome.evidence is not None:
            ev = outcome.evidence
            seq = await self.store.add_evidence(
                task_id, kind=ev.kind, summary=ev.summary, content=ev.content, meta=ev.meta
            )
            outcome.content = (
                f"[E{seq}] {ev.summary}\n{DATA_OPEN}\n{clip(outcome.content)}\n{DATA_CLOSE}"
            )
        await self._tool_step(
            task_id,
            call,
            outcome.content.split("\n", 1)[0][:300],
            ok=outcome.ok,
            evidence_seq=seq,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        return outcome

    async def _tool_step(
        self,
        task_id: uuid.UUID,
        call: dict[str, Any],
        summary: str,
        *,
        ok: bool,
        evidence_seq: int | None = None,
        duration_ms: int = 0,
    ) -> None:
        await self.store.add_step(
            task_id,
            kind="tool_call",
            name=call["name"],
            input=call.get("input") or {},
            summary=summary,
            ok=ok,
            evidence_seq=evidence_seq,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ resume after a decision

    async def _resume(
        self, task: Task, ctx: ToolContext, registry: ToolRegistry, messages: list[dict[str, Any]]
    ) -> Flow:
        pending = task.pending or {}
        approval = await self.store.get_approval(uuid.UUID(pending["approval_id"]))
        if approval is None or approval.status == "pending":
            return "paused"
        approved = approval.status == "approved"
        await self.store.add_step(
            task.id,
            kind="approval",
            name=approval.action,
            summary=f"{'approved' if approved else 'rejected'} by user"
            + (f": {approval.reason}" if approval.reason else ""),
        )
        await self.store.update(task.id, pending=None, status="running")

        if pending["type"] == "plan":
            text = (
                "The user approved your plan. Proceed."
                if approved
                else f"The user rejected your plan. Reason: {approval.reason or 'none given'}. "
                "Revise it with submit_plan, or finish if you cannot proceed."
            )
            last = messages[-1]
            last["content"] = [*last["content"], {"type": "text", "text": text}]
            await self.store.update(task.id, conversation=messages)
            return "continue"

        calls: list[dict[str, Any]] = pending["calls"]
        index: int = pending["index"]
        results: list[dict[str, Any]] = pending["results"]
        if approved:
            calls[index]["approved"] = True
        else:
            results.append(
                _result_block(
                    calls[index]["id"],
                    f"The user rejected this action. Reason: {approval.reason or 'none given'}. "
                    "Do not retry the same action; choose another approach or finish.",
                    error=True,
                )
            )
            await self._tool_step(task.id, calls[index], "rejected by user", ok=True)
            index += 1
        return await self._process(
            task.id,
            ctx,
            registry,
            messages,
            calls,
            index,
            results,
            bool(pending.get("plan_pending")),
        )


def _plan_preview(plan: dict[str, Any]) -> str:
    lines = [f"Objective: {plan.get('objective', '')}"]
    for h in plan.get("hypotheses", []):
        lines.append(f"- hypothesis: {h.get('statement')} (check: {h.get('how_to_check')})")
    for i, s in enumerate(plan.get("steps", []), 1):
        lines.append(f"{i}. {s}")
    if plan.get("acceptance_criteria"):
        lines.append("Acceptance: " + "; ".join(plan["acceptance_criteria"]))
    if plan.get("risks"):
        lines.append("Risks: " + "; ".join(plan["risks"]))
    return "\n".join(lines)


__all__ = ["AgentLoop", "ToolCall"]

# Decisions

Short architecture decision records. Newest last. Each one says what was decided, why,
and what would make us revisit it.

## ADR-001 Modular monolith (22 Oct 2026)

One FastAPI process and one Postgres. No microservices, queues or Kubernetes.
**Why:** one developer, one local user; every extra moving part is something the
benchmark has to account for. **Revisit when:** task runs need to outlive the API
process, or several users run tasks at once.

## ADR-002 Plain Anthropic SDK tool use, no agent framework

The agent loop is written directly against the SDK's tool-use API: model requests a tool,
our code validates, executes and returns. No LangChain or LangGraph.
**Why:** the loop is about 150 lines; owning it makes every step observable, testable
with a scripted FakeLLM, and attributable in evaluation. A framework would hide exactly
the parts we want to measure. **Revisit when:** we need features a framework gives for
free and the benchmark shows they matter.

## ADR-003 The agent never touches the user's working tree

Each task works in its own `git worktree` on branch `agent/<task-id>`. Undo means
deleting the worktree. Applying a fix to the user's checkout is a separate, approved step
using `git apply --3way --check`. **Why:** "do not overwrite user changes or reset a dirty
working tree" is a hard requirement, and isolation enforces it structurally instead of
through careful code paths.

## ADR-004 Anthropic message format at the LLM boundary

The `LLM` protocol hides the SDK but keeps Anthropic's content-block message shape.
**Why:** it is the only provider in scope. A "neutral" format with one implementation
is untested abstraction. **Revisit when:** a second provider is actually added.

## ADR-005 Deterministic retrieval first, embeddings only on evidence

File discovery uses `git ls-files`, exact/regex search and a Python `ast` symbol index.
No vector database. **Why:** code search is dominated by exact identifiers, where lexical
search is precise and cheap. **Revisit when:** the M7 benchmark shows tasks failing
because the right file was never found.

## ADR-006 Our retry policy, not the SDK's

The SDK client runs with `max_retries=0`. Retries (full-jitter exponential backoff) and a
circuit breaker live in `llm/resilience.py`. **Why:** one retry policy, tested with a
fake clock, plus a breaker so an outage stops a task quickly instead of draining its time
budget. Credit-exhausted and auth errors are never retried.

## ADR-007 No shell tool; tests via an allowlisted runner in Docker

The model can never run an arbitrary command. Tests run through `run_approved_test`
(pytest first) inside a container with no network and resource limits. **Why:**
repository code is untrusted, and "never run it on the host" is a design rule, not a
config option.

## ADR-008 Status vocabulary separates "fixed" from "verified"

`fixed_unverified` and `verified` are different task states, and only the verification
engine, from recorded exit codes, can set `verified`. The database constrains the status
column. **Why:** the project's central claim is that it never reports an unproven success.

## ADR-009 Ports: API 8010, Postgres 5434

**Why:** avoid clashing with eap (8000/5432) and rag-curriculum running on the same
machine. Postgres binds to 127.0.0.1 only.

## ADR-010 Anthropic SDK 2.x uses httpx2

SDK 2.x moved to the `httpx2` package and rejects `httpx` client objects. The adapter and
its tests import `httpx2` directly, and it is a declared dependency. **Why recorded:**
this broke the first test run, and anyone porting eap's adapter will hit it.

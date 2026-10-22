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

Each task works in its own `git worktree` on branch `verifix/<id12>`, whose baseline
commit is HEAD plus the user's uncommitted changes. Undo means deleting the worktree.
Applying a fix to the user's checkout is a separate, confirmed step: it refuses if any
touched file changed since the snapshot, then runs `git apply --check` before writing. **Why:** "do not overwrite user changes or reset a dirty
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

The model can never run an arbitrary command. Tests run through `run_tests`
(pytest, validated node ids and `-k` only) inside a container with no network and resource limits. **Why:**
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

## ADR-011 Edits are exact, unique text replacements

`edit_file` replaces `old_text` that must occur exactly once (CRLF preserved); otherwise
the request is rejected before any approval is asked. **Why:** line-number patches drift
as soon as the file changes; a unique anchor either applies exactly or fails loudly, and
the dry run produces the exact diff the user approves.

## ADR-012 Read-only diff views use a private git index

Diffing the workspace stages into a temporary `GIT_INDEX_FILE`, never the worktree's real
index. **Why:** the UI polls the diff while the agent works; sharing the index caused
`index.lock` races in testing.

## ADR-013 A runtime image instead of trusting the base image

Stock `python:3.12-slim` has no pytest. verifix builds `verifix-runtime:<hash>` (base +
pytest) once, via `python -m verifix setup-sandbox` or an approved `prepare_environment`;
if the base already has pytest it is used as is. **Why:** otherwise every run on a
no-dependency repository fails with "No module named pytest", which looks like a test
failure.

## ADR-014 Pull requests carry the agent's patch only

The PR branch is built from the task's `source_head` plus the workspace diff, in a
throwaway worktree, and opened as a draft. **Why:** the workspace baseline contains the
user's uncommitted work, which must never be pushed because they asked for a PR.

## ADR-015 Benchmark grading is independent of the agent

The grader applies the patch to a fresh copy, adds hidden tests and runs everything in
the sandbox; it never reads the agent's report. The ruler is validated every run
(reference 100%, empty patch 0%, hidden tests fail on the bug). **Why:** a benchmark that
trusts the agent cannot measure false "verified" claims, the failure that matters most here.

## ADR-016 Cassettes replay by position

Recorded responses are served in order, and replay stops if the conversation length
differs from the recording. **Why:** tool results contain timings and paths, so exact
request matching would never hit; a length check still catches a diverged run.

## ADR-017 UI port 5178

**Why:** Vite's default 5173 is usually taken by another local project (several VS Code
windows run at once). The dev server falls back to the next free port.

# Architecture

A modular monolith: one FastAPI process, one Postgres, and one short-lived Docker
container per test run.

```
React UI ──/api──▶ FastAPI ──▶ TaskManager (asyncio task per running task)
                      │              │
                      │              ▼
                      │         AgentLoop ──respond()──▶ LLM (Anthropic | Fake | Replay)
                      │              │ tool request
                      │              ▼
                      │      validate (Pydantic) ─▶ prepare (dry run + preview) ─▶ policy
                      │              │                                   │ ask
                      │              │ allow                             ▼
                      │              ▼                         Approval row; task pauses
                      │         Tool handler                   (resumes on POST /approvals)
                      │      ┌──────┼──────────────┬──────────────────┐
                      │      ▼      ▼              ▼                  ▼
                      │  RepoJail  Workspace     DockerSandbox      report reconcile
                      │  (reads)   (git worktree) (pytest, no net)  (sets verified)
                      ▼
                  Postgres: repos · tasks · evidence · steps · approvals · findings · eval_runs
```

## The agent loop (`agent/loop.py`)

1. The first message holds the request, a repository overview and the policy/budget.
   Repository content always reaches the model wrapped as untrusted data.
2. Each model turn is recorded as a `model_call` step (tokens, cost).
3. Each tool request is validated against its Pydantic schema, then `prepare()` checks it
   against current state (for an edit: the old text occurs exactly once) and builds the
   preview a human sees.
4. If the policy says `ask` for that permission class (`write`, `execute`, `install`),
   the loop saves its full state (conversation, the pending calls, results so far) in
   `task.pending`, creates an approval and returns. The task is `awaiting_approval`;
   nothing is held in memory.
5. Otherwise the tool runs. Its output is stored as evidence `E<n>` (redacted, SHA-256)
   and returned to the model as `[E<n>]` inside untrusted-data markers.
6. The loop stops on `finish`, a budget limit (model calls, tool calls, tokens, USD,
   minutes), cancellation, a non-retryable model error, or the model repeatedly not
   calling tools.

`finish` does not set the status. `agent/report.py` reconciles the model's claims with
the evidence ledger: `verified` only when the last test run after the last edit passed;
`fixed_unverified` otherwise; "reproduced" only with a recorded failing run; every claim
is checked for evidence ids that exist; evidence coverage is computed.

## Workspaces (`agent/workspace.py`)

Each fix task gets `git worktree add` on branch `verifix/<id12>` under
`~/.verifix/workspaces`. Its baseline commit is HEAD plus the user's uncommitted tracked
changes and untracked non-secret files, so the agent sees what the user sees. Read-only
diff views use a private temporary `GIT_INDEX_FILE`, so the UI polling the diff never
races the agent. Applying to the user's checkout refuses if any touched file changed
since the snapshot, then runs `git apply --check` before writing.

## Sandbox (`sandbox/docker.py`)

Images: `verifix-runtime:<hash>` (base + pytest) and `verifix-env:<manifest hash>`
(runtime + the repository's declared dependencies, built only with approval). A run is
`docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt
no-new-privileges --user 1000:1000 --memory --cpus --pids-limit -v worktree:/work:ro`,
with a wall-clock kill. The model supplies only validated test node ids and a `-k`
expression. Status comes from the pytest exit code.

## Benchmark (`evals/`)

`cases/<id>/` holds a buggy repo, hidden tests and a reference fix. A solver produces a
patch (reference, no-op, or the real agent loop in autonomous mode); the grader applies
it to a fresh copy, adds the hidden tests and runs them plus the whole suite in the
sandbox. Grading never reads the agent's report, which is how "false verified" is
measured. Model responses can be recorded to and replayed from JSONL cassettes.

## GitHub (`github/`)

Import clones `https://github.com/<owner>/<repo>` into `~/.verifix/repos` and registers
it. A PR branch is built from the task's `source_head` plus the agent's patch only (never
the user's uncommitted work), pushed, and opened as a draft whose body lists the claims
with their evidence ids. The token travels in `GIT_CONFIG_*` environment variables and an
`Authorization` header, never in argv or URLs.

## Data model

| Table | Holds |
|---|---|
| repos | registered checkouts (unique resolved path) |
| tasks | request, kind, status (DB-constrained), policy, budget, conversation, pending state, plan, report, workspace, counters |
| evidence | per-task numbered, redacted outputs with SHA-256; reports cite them |
| steps | ordered timeline: model calls, tool calls, approvals, status changes, with tokens/cost/duration |
| approvals | pending / approved / rejected decisions with preview and reason |
| findings | review results: severity, location, explanation, recommendation, evidence, confidence |
| eval_runs | benchmark runs: config, summary metrics, per-case results |

## Why no LangChain, LangGraph, vector DB, Redis or Temporal

The loop is a few hundred lines, fully observable and testable with a scripted model.
State lives in Postgres, so pausing for a human needs no workflow engine. Code search is
lexical plus an AST symbol index. Each is reconsidered when a benchmark number shows a gap
it would close (ADR-002, ADR-005).

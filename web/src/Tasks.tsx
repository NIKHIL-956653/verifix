import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  TERMINAL,
  type EvidenceItem,
  type Finding,
  type Repo,
  type Step,
  type Task,
  type TaskKind,
} from "./api";
import {
  ago,
  Diff,
  EChip,
  errText,
  EvidenceCtx,
  go,
  money,
  Notice,
  pct,
  StatusDot,
  statusLabel,
  Verdict,
  WithChips,
} from "./ui";

// ================================================================== list + new

export function TasksPage({ taskId, repos }: { taskId: string | null; repos: Repo[] }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .tasks()
      .then((t) => {
        setTasks(t);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <div className="split">
      <aside className="rail">
        <button type="button" className="btn primary wide" onClick={() => go("/tasks/new")}>
          New task
        </button>
        {error && <Notice kind="error">{error}</Notice>}
        {tasks && tasks.length === 0 && <p className="empty">No tasks yet. Start one to see it here.</p>}
        <ul className="tasklist">
          {(tasks ?? []).map((t) => (
            <li key={t.id}>
              <a href={`#/tasks/${t.id}`} className={t.id === taskId ? "current" : ""}>
                <span className="tl-title">
                  <StatusDot status={t.status} />
                  {t.title}
                </span>
                <span className="tl-meta">
                  {t.kind} · {statusLabel(t.status)} · {ago(t.created_at)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      </aside>
      <section className="main">
        {taskId === "new" ? (
          <NewTask repos={repos} onCreated={load} />
        ) : taskId ? (
          <TaskDetail key={taskId} id={taskId} onChange={load} />
        ) : (
          <div className="blank">
            <h2>Pick a task, or start one</h2>
            <p>
              Each task runs in an isolated copy of your repository. Every claim in its report links
              to the evidence that backs it.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

const KINDS: { kind: TaskKind; label: string; hint: string }[] = [
  { kind: "ask", label: "Ask", hint: "Answer a question about the code. Read-only." },
  { kind: "investigate", label: "Investigate", hint: "Find a bug's root cause and reproduce it. No edits." },
  { kind: "fix", label: "Fix", hint: "Plan, change code in a workspace copy, and verify with tests." },
  { kind: "review", label: "Review", hint: "Review your uncommitted changes and report findings." },
];

function NewTask({ repos, onCreated }: { repos: Repo[]; onCreated: () => void }) {
  const [repoId, setRepoId] = useState(repos[0]?.id ?? "");
  const [kind, setKind] = useState<TaskKind>("fix");
  const [request, setRequest] = useState("");
  const [askPlan, setAskPlan] = useState(true);
  const [askWrite, setAskWrite] = useState(true);
  const [askInstall, setAskInstall] = useState(true);
  const [maxCost, setMaxCost] = useState(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!repoId && repos[0]) setRepoId(repos[0].id);
  }, [repos, repoId]);

  if (repos.length === 0)
    return (
      <div className="blank">
        <h2>Add a repository first</h2>
        <p>Tasks run against a local Git repository you register.</p>
        <button type="button" className="btn primary" onClick={() => go("/repos")}>
          Add a repository
        </button>
      </div>
    );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const t = await api.createTask({
        repo_id: repoId,
        kind,
        request,
        policy: {
          approve_plan: kind === "fix" && askPlan,
          write: askWrite ? "ask" : "auto",
          execute: "auto",
          install: askInstall ? "ask" : "auto",
        },
        budget: { max_cost_usd: maxCost },
      });
      onCreated();
      go(`/tasks/${t.id}`);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="newtask" onSubmit={submit}>
      <h2>New task</h2>
      <label className="field">
        <span>Repository</span>
        <select value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name} — {r.root_path}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="kinds">
        <legend>What should it do?</legend>
        {KINDS.map((k) => (
          <label key={k.kind} className={kind === k.kind ? "kind on" : "kind"}>
            <input
              type="radio"
              name="kind"
              value={k.kind}
              checked={kind === k.kind}
              onChange={() => setKind(k.kind)}
            />
            <strong>{k.label}</strong>
            <span>{k.hint}</span>
          </label>
        ))}
      </fieldset>

      <label className="field">
        <span>Describe it the way you would to a colleague</span>
        <textarea
          required
          minLength={3}
          rows={5}
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="POST /login returns 500 when the email field is missing. It should return a validation error."
        />
      </label>

      <fieldset className="checks">
        <legend>Ask me before</legend>
        {kind === "fix" && (
          <label>
            <input type="checkbox" checked={askPlan} onChange={(e) => setAskPlan(e.target.checked)} />
            starting on the plan
          </label>
        )}
        {kind === "fix" && (
          <label>
            <input type="checkbox" checked={askWrite} onChange={(e) => setAskWrite(e.target.checked)} />
            each file change (in the workspace copy)
          </label>
        )}
        <label>
          <input type="checkbox" checked={askInstall} onChange={(e) => setAskInstall(e.target.checked)} />
          installing dependencies (uses the network)
        </label>
      </fieldset>

      <label className="field narrow">
        <span>Spending limit for this task (USD)</span>
        <input
          type="number"
          min={0.05}
          max={100}
          step={0.05}
          value={maxCost}
          onChange={(e) => setMaxCost(Number(e.target.value))}
        />
      </label>

      {error && <Notice kind="error">{error}</Notice>}
      <button type="submit" className="btn primary" disabled={busy || !repoId}>
        {busy ? "Starting…" : "Start task"}
      </button>
    </form>
  );
}

// ================================================================== detail

type Tab = "timeline" | "changes" | "evidence" | "findings" | "report";

function TaskDetail({ id, onChange }: { id: string; onChange: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const [sheet, setSheet] = useState<number | null>(null);
  const lastSeq = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([api.task(id), api.steps(id, lastSeq.current)]);
      setTask(t);
      if (s.length) {
        lastSeq.current = Math.max(lastSeq.current, s[s.length - 1].seq);
        setSteps((prev) => {
          const seen = new Set(prev.map((x) => x.seq));
          return [...prev, ...s.filter((x) => !seen.has(x.seq))];
        });
      }
      setError(null);
      return t;
    } catch (e) {
      setError(errText(e));
      return null;
    }
  }, [id]);

  // One polling loop at a time; it stops when the task reaches a final state and is
  // restarted after any action that can make it run again (approve, retry).
  const polling = useRef(false);
  const alive = useRef(true);
  const poll = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      while (alive.current) {
        const t = await refresh();
        if (t && TERMINAL.has(t.status)) break;
        await new Promise((r) => window.setTimeout(r, 1500));
      }
    } finally {
      polling.current = false;
    }
  }, [refresh]);

  useEffect(() => {
    alive.current = true;
    poll();
    return () => {
      alive.current = false;
    };
  }, [poll]);

  // A terminal fix task opens on its result; others on the timeline.
  const opened = useRef(false);
  useEffect(() => {
    if (task && !opened.current && TERMINAL.has(task.status)) {
      opened.current = true;
      if (task.report) setTab("report");
    }
  }, [task]);

  const restart = async () => {
    await refresh();
    onChange();
    poll();
  };

  if (!task) return error ? <Notice kind="error">{error}</Notice> : <p className="empty">Loading…</p>;

  const tabs: Tab[] = ["timeline", "report", "evidence"];
  if (task.kind === "fix") tabs.splice(1, 0, "changes");
  if (task.kind === "review") tabs.splice(1, 0, "findings");

  return (
    <EvidenceCtx.Provider value={setSheet}>
      <article className="task">
        <header className="task-head">
          <div className="task-title">
            <h2>{task.title}</h2>
            <p className="request">{task.request}</p>
          </div>
          <Verdict status={task.status} />
        </header>

        <Meters task={task} live={["running", "planning", "queued"].includes(task.status)} />
        {task.error && task.status !== "cancelled" && <Notice kind="error">{task.error}</Notice>}
        {task.pending_approval && <ApprovalPanel task={task} onDecided={restart} />}
        <TaskActions task={task} onChange={restart} />

        <nav className="tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              className={tab === t ? "tab on" : "tab"}
              onClick={() => setTab(t)}
            >
              {t === "changes" ? "Changes" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>

        {tab === "timeline" && (
          <Timeline
            steps={steps}
            live={["running", "planning", "queued"].includes(task.status)}
            plan={task.plan}
          />
        )}
        {tab === "changes" && <Changes id={task.id} status={task.status} />}
        {tab === "evidence" && <EvidenceList id={task.id} status={task.status} />}
        {tab === "findings" && <Findings id={task.id} status={task.status} />}
        {tab === "report" && <ReportView task={task} />}
      </article>
      {sheet !== null && <EvidenceSheet taskId={task.id} seq={sheet} onClose={() => setSheet(null)} />}
    </EvidenceCtx.Provider>
  );
}

function Meters({ task, live }: { task: Task; live: boolean }) {
  const b = task.budget ?? {};
  return (
    <dl className="meters">
      <div>
        <dt>Model calls</dt>
        <dd>
          {task.model_calls}
          <small> / {b.max_model_calls ?? "–"}</small>
        </dd>
      </div>
      <div>
        <dt>Tool calls</dt>
        <dd>
          {task.tool_calls}
          <small> / {b.max_tool_calls ?? "–"}</small>
        </dd>
      </div>
      <div>
        <dt>Cost</dt>
        <dd>
          {money(task.cost_usd)}
          <small> / {money(b.max_cost_usd)}</small>
        </dd>
      </div>
      <div>
        <dt>Time</dt>
        <dd>
          {Math.round(task.active_seconds)}s{live && <span className="pulse" aria-label="running" />}
        </dd>
      </div>
    </dl>
  );
}

function ApprovalPanel({ task, onDecided }: { task: Task; onDecided: () => void }) {
  const a = task.pending_approval!;
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.decide(task.id, a.id, approve, reason);
      onDecided();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  const what =
    a.action === "plan"
      ? "Approve the plan before it starts working"
      : a.action === "edit_file" || a.action === "create_file" || a.action === "revert_file"
        ? `Allow this change to ${(a.payload as { input?: { path?: string } }).input?.path ?? "a file"}`
        : a.action === "prepare_environment"
          ? "Allow installing the repository's dependencies (network access during the build only)"
          : `Allow ${a.action}`;
  return (
    <section className="approval-panel" aria-live="polite">
      <h3>{what}</h3>
      {a.preview &&
        (a.preview.startsWith("---") || a.preview.startsWith("diff") ? (
          <Diff text={a.preview} />
        ) : (
          <pre className="preview">{a.preview}</pre>
        ))}
      <label className="field">
        <span>Note for the agent (optional, shown to it if you reject)</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="row">
        <button type="button" className="btn good" disabled={busy} onClick={() => decide(true)}>
          Approve
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => decide(false)}>
          Reject
        </button>
      </div>
    </section>
  );
}

function TaskActions({ task, onChange }: { task: Task; onChange: () => void }) {
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true);
    setMsg(null);
    try {
      const text = await fn();
      if (text) setMsg({ kind: "info", text });
      onChange();
    } catch (e) {
      setMsg({ kind: "error", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };
  const live = !TERMINAL.has(task.status);
  const changed = ["verified", "fixed_unverified"].includes(task.status) && !!task.worktree_path;
  const pr = task.report?.pull_request;
  return (
    <div className="actions">
      <div className="row">
        {live && (
          <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.cancel(task.id).then(() => {}))}>
            Cancel task
          </button>
        )}
        {["failed", "budget_exhausted"].includes(task.status) && (
          <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.retry(task.id).then(() => {}))}>
            Retry from where it stopped
          </button>
        )}
        {changed && (
          <>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "Apply these changes to the files in your repository? Nothing is staged or committed.",
                  )
                )
                  run(async () => {
                    const r = await api.apply(task.id);
                    if (!r.applied) throw new Error(`${r.message}${r.conflicts.length ? `: ${r.conflicts.join(", ")}` : ""}`);
                    return r.message;
                  });
              }}
            >
              Apply to my checkout
            </button>
            <a className="btn" href={`/api/tasks/${task.id}/patch`} download>
              Download patch
            </a>
            {!pr && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      "Push a new branch with only these changes to GitHub and open a draft pull request?",
                    )
                  )
                    run(async () => {
                      const r = await api.openPr(task.id);
                      return `Opened draft PR #${r.number} on branch ${r.branch}.`;
                    });
                }}
              >
                Open draft PR
              </button>
            )}
          </>
        )}
        {pr && <PrBadge taskId={task.id} url={pr.url} number={pr.number} />}
        {TERMINAL.has(task.status) && task.worktree_path && (
          <button
            type="button"
            className="btn quiet"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Delete this task's workspace copy? Your repository is not touched."))
                run(() => api.removeWorkspace(task.id).then(() => "Workspace removed."));
            }}
          >
            Remove workspace
          </button>
        )}
      </div>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
    </div>
  );
}

function PrBadge({ taskId, url, number }: { taskId: string; url: string; number: number }) {
  const [overall, setOverall] = useState<string | null>(null);
  useEffect(() => {
    api
      .prChecks(taskId)
      .then((c) => setOverall(c.overall))
      .catch(() => setOverall(null));
  }, [taskId]);
  return (
    <a className="btn quiet" href={url} target="_blank" rel="noreferrer">
      PR #{number}
      {overall && ` · checks: ${overall}`}
    </a>
  );
}

// ------------------------------------------------------------------ timeline

function Timeline({ steps, live, plan }: { steps: Step[]; live: boolean; plan: Task["plan"] }) {
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (live) end.current?.scrollIntoView({ block: "nearest" });
  }, [steps.length, live]);
  return (
    <div>
      {plan?.objective && (
        <section className="plan">
          <h3>Plan</h3>
          <p>{plan.objective}</p>
          {plan.steps && (
            <ol>
              {plan.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          )}
          {plan.acceptance_criteria?.length ? (
            <p className="muted">Done when: {plan.acceptance_criteria.join("; ")}</p>
          ) : null}
        </section>
      )}
      <ol className="timeline">
        {steps.map((s) => (
          <li key={s.seq} className={`st ${s.kind} ${s.ok ? "" : "bad"}`}>
            <span className="st-kind">
              {s.kind === "model_call"
                ? "Model"
                : s.kind === "tool_call"
                  ? s.name
                  : s.kind === "approval"
                    ? "You"
                    : "Status"}
            </span>
            <span className="st-body">
              {s.evidence !== null && !s.summary.includes(`[E${s.evidence}]`) && <EChip seq={s.evidence} />}{" "}
              <WithChips text={s.summary} />
            </span>
            <span className="st-cost">
              {s.kind === "model_call"
                ? `${(s.input_tokens / 1000).toFixed(1)}k in · ${money(s.cost_usd)}`
                : s.duration_ms
                  ? `${(s.duration_ms / 1000).toFixed(1)}s`
                  : ""}
            </span>
          </li>
        ))}
        {live && (
          <li ref={end} className="st waiting">
            <span className="st-kind">…</span>
            <span className="st-body">working</span>
            <span />
          </li>
        )}
      </ol>
      {!steps.length && !live && <p className="empty">Nothing recorded.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ tabs

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let off = false;
    fn()
      .then((d) => !off && setData(d))
      .catch((e) => !off && setError(errText(e)));
    return () => {
      off = true;
    };
  }, deps);
  return { data, error };
}

function Changes({ id, status }: { id: string; status: string }) {
  const { data, error } = useLoad(() => api.diff(id), [id, status]);
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!data) return <p className="empty">Loading…</p>;
  return (
    <div>
      <p className="muted">
        {data.stats.files} files · +{data.stats.added} −{data.stats.removed} · in the workspace copy, not
        your checkout
      </p>
      <Diff text={data.diff} />
    </div>
  );
}

function EvidenceList({ id, status }: { id: string; status: string }) {
  const { data, error } = useLoad(() => api.evidence(id), [id, status]);
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!data) return <p className="empty">Loading…</p>;
  if (!data.length) return <p className="empty">No evidence recorded yet.</p>;
  return (
    <table className="ledger">
      <thead>
        <tr>
          <th scope="col">Id</th>
          <th scope="col">Kind</th>
          <th scope="col">What it shows</th>
          <th scope="col">SHA-256</th>
        </tr>
      </thead>
      <tbody>
        {data.map((e) => (
          <tr key={e.seq}>
            <td>
              <EChip seq={e.seq} />
            </td>
            <td>{e.kind.replace("_", " ")}</td>
            <td>{e.summary}</td>
            <td className="hash">{e.sha256.slice(0, 12)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Findings({ id, status }: { id: string; status: string }) {
  const { data, error } = useLoad<Finding[]>(() => api.findings(id), [id, status]);
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!data) return <p className="empty">Loading…</p>;
  if (!data.length) return <p className="empty">No findings.</p>;
  return (
    <ul className="findings">
      {data.map((f) => (
        <li key={f.id} className={`finding sev-${f.severity}`}>
          <div className="f-head">
            <span className="sev">{f.severity}</span>
            <strong>{f.title}</strong>
            <span className={`conf ${f.confidence}`}>{f.confidence}</span>
          </div>
          <code className="loc">
            {f.path}:{f.line_start}
            {f.line_end !== f.line_start ? `–${f.line_end}` : ""}
          </code>
          <p>{f.explanation}</p>
          <p>
            <em>Recommendation:</em> {f.recommendation}
          </p>
          <p className="cites">
            Evidence: {f.evidence.map((n) => <EChip key={n} seq={n} />)}
            {f.note && <span className="muted"> {f.note}</span>}
          </p>
        </li>
      ))}
    </ul>
  );
}

function ReportView({ task }: { task: Task }) {
  const r = task.report;
  if (!r) return <p className="empty">The report appears when the task finishes.</p>;
  const v = r.verification;
  return (
    <div className="report">
      {r.summary && <p className="lead">{r.summary}</p>}
      {r.root_cause && (
        <section>
          <h3>Root cause</h3>
          <p>{r.root_cause}</p>
        </section>
      )}
      <section>
        <h3>Claims</h3>
        <p className="muted">
          {pct(r.evidence_coverage)} of claims are backed by recorded evidence.
        </p>
        <ul className="claims">
          {(r.claims ?? []).map((c, i) => (
            <li key={i} className={c.supported ? "ok" : "unsupported"}>
              <span>{c.statement}</span>
              <span className="cites">
                {c.evidence.map((n) => (
                  <EChip key={n} seq={n} missing={c.missing.includes(n)} />
                ))}
                {!c.supported && <span className="flag">not supported</span>}
              </span>
            </li>
          ))}
        </ul>
      </section>
      {v && (
        <section>
          <h3>Verification</h3>
          <dl className="facts">
            <dt>Test runs</dt>
            <dd>
              {v.test_runs} ({v.failing_runs} failing)
            </dd>
            <dt>Reproduced before fixing</dt>
            <dd>{v.reproduced_before_fix ? "yes" : "no"}</dd>
            <dt>Last run after the last change</dt>
            <dd>
              {v.last_run_after_last_change ? (
                <>
                  {v.last_run_after_last_change.status} <EChip seq={v.last_run_after_last_change.evidence} />
                </>
              ) : (
                "none"
              )}
            </dd>
          </dl>
        </section>
      )}
      {r.notes?.length ? (
        <section>
          <h3>Adjusted by verifix</h3>
          <ul>
            {r.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
          {r.claimed_outcome && r.claimed_outcome !== r.outcome && (
            <p className="muted">
              The agent claimed “{r.claimed_outcome}”; recorded as “{r.outcome}”.
            </p>
          )}
        </section>
      ) : null}
      {r.uncertainty && (
        <section>
          <h3>What is still uncertain</h3>
          <p>{r.uncertainty}</p>
        </section>
      )}
      {r.next_steps?.length ? (
        <section>
          <h3>Suggested next steps</h3>
          <ul>
            {r.next_steps.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ evidence sheet

function EvidenceSheet({ taskId, seq, onClose }: { taskId: string; seq: number; onClose: () => void }) {
  const [item, setItem] = useState<EvidenceItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    setItem(null);
    setError(null);
    api
      .evidenceItem(taskId, seq)
      .then(setItem)
      .catch((e) => setError(errText(e)));
  }, [taskId, seq]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const meta = item?.meta ?? {};
  return (
    <aside className="sheet" role="dialog" aria-label={`Evidence E${seq}`}>
      <header>
        <h3>
          <span className="echip static">E{seq}</span> {item?.kind.replace("_", " ")}
        </h3>
        <button ref={closeRef} type="button" className="btn quiet" onClick={onClose}>
          Close
        </button>
      </header>
      {error && <Notice kind="error">{error}</Notice>}
      {item && (
        <>
          <p>{item.summary}</p>
          {"command" in meta && meta.command ? (
            <p className="muted">
              Ran <code>{String(meta.command)}</code>
              {"exit_code" in meta ? ` · exit ${String(meta.exit_code)}` : ""}
            </p>
          ) : null}
          {item.content?.startsWith("--- ") || item.content?.startsWith("diff ") ? (
            <Diff text={item.content} />
          ) : (
            <pre className="evidence-body">{item.content}</pre>
          )}
          <p className="hash">sha256 {item.sha256}</p>
        </>
      )}
    </aside>
  );
}

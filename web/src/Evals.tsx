import { useCallback, useEffect, useState } from "react";
import { api, type BenchCase, type EvalRun } from "./api";
import { ago, errText, go, money, Notice, pct } from "./ui";

export function EvalsPage({ runId }: { runId: string | null }) {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [cases, setCases] = useState<BenchCase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .evalRuns()
      .then(setRuns)
      .catch((e) => setError(errText(e)));
  }, []);

  useEffect(() => {
    load();
    api.benchCases().then(setCases).catch(() => {});
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [load]);

  const start = async (solver: "reference" | "noop" | "agent") => {
    if (
      solver === "agent" &&
      !window.confirm(
        `Run the agent on all ${cases.length} cases? This spends Anthropic credit (up to $1 per case).`,
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.startEval(solver, solver === "agent");
      load();
      go(`/evals/${run.id}`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="split">
      <aside className="rail">
        <div className="runbtns">
          <button type="button" className="btn primary wide" disabled={busy} onClick={() => start("agent")}>
            Run the agent
          </button>
          <button type="button" className="btn wide" disabled={busy} onClick={() => start("reference")}>
            Grader check (expects 100%)
          </button>
          <button type="button" className="btn wide" disabled={busy} onClick={() => start("noop")}>
            Test check (expects 0%)
          </button>
        </div>
        {error && <Notice kind="error">{error}</Notice>}
        <ul className="tasklist">
          {(runs ?? []).map((r) => (
            <li key={r.id}>
              <a href={`#/evals/${r.id}`} className={r.id === runId ? "current" : ""}>
                <span className="tl-title">
                  {r.name}
                  <span className="score">{r.summary.state === "running" ? "…" : pct(r.summary.success_rate)}</span>
                </span>
                <span className="tl-meta">
                  {r.config.solver} · {r.mode} · {ago(r.created_at)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      </aside>
      <section className="main">
        {runId ? <RunView key={runId} id={runId} /> : <BenchIntro cases={cases} />}
      </section>
    </div>
  );
}

function BenchIntro({ cases }: { cases: BenchCase[] }) {
  return (
    <div className="bench-intro">
      <h2>Reliability benchmark</h2>
      <p>
        Each case is a small repository with one seeded bug and hidden tests the agent never sees. A
        case counts as solved only when the agent's patch passes the hidden tests without breaking the
        existing ones. The grader ignores what the agent says about its own work, so it can catch an
        agent that claims “verified” and is wrong.
      </p>
      <table className="ledger">
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Kind of bug</th>
            <th scope="col">What a user reported</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.id}>
              <td>{c.title}</td>
              <td>{c.category}</td>
              <td className="muted">{c.request}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunView({ id }: { id: string }) {
  const [run, setRun] = useState<EvalRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    let t = 0;
    const tick = () =>
      api
        .evalRun(id)
        .then((r) => {
          if (stop) return;
          setRun(r);
          if (r.summary.state === "running") t = window.setTimeout(tick, 2000);
        })
        .catch((e) => setError(errText(e)));
    tick();
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, [id]);
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!run) return <p className="empty">Loading…</p>;
  const s = run.summary;
  const isAgent = run.config.solver === "agent";
  return (
    <div>
      <header className="task-head">
        <div className="task-title">
          <h2>{run.name}</h2>
          <p className="request">
            {run.config.solver} solver · {run.mode} · {new Date(run.created_at).toLocaleString()}
          </p>
        </div>
        <div className={`verdict ${s.state === "failed" ? "bad" : s.state === "running" ? "live" : "plain"}`}>
          <span className="verdict-label">
            {s.state === "running" ? `${s.done ?? 0} of ${s.cases ?? "?"} graded` : pct(s.success_rate)}
          </span>
          <span className="verdict-note">
            {s.state === "failed" ? s.error : `${s.successes ?? 0} of ${s.cases ?? 0} cases solved`}
          </span>
        </div>
      </header>

      <dl className="meters">
        <div>
          <dt>Patch applies</dt>
          <dd>{pct(s.patch_applies_rate)}</dd>
        </div>
        <div>
          <dt>Cases with regressions</dt>
          <dd>{s.cases_with_regressions ?? "–"}</dd>
        </div>
        <div>
          <dt>Patch precision</dt>
          <dd>{pct(s.mean_patch_precision)}</dd>
        </div>
        {isAgent && (
          <>
            <div className={s.false_verified ? "alarm" : ""}>
              <dt>False “verified”</dt>
              <dd>
                {s.false_verified ?? 0}
                <small> of {s.claimed_verified ?? 0} claimed</small>
              </dd>
            </div>
            <div>
              <dt>Evidence coverage</dt>
              <dd>{pct(s.mean_evidence_coverage)}</dd>
            </div>
            <div>
              <dt>Tool reliability</dt>
              <dd>{pct(s.tool_reliability)}</dd>
            </div>
            <div>
              <dt>Cost</dt>
              <dd>{money(s.total_cost_usd)}</dd>
            </div>
          </>
        )}
        <div>
          <dt>Mean time</dt>
          <dd>{s.mean_duration_s !== undefined ? `${s.mean_duration_s}s` : "–"}</dd>
        </div>
      </dl>

      <table className="ledger results">
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Graded</th>
            {isAgent && <th scope="col">Agent said</th>}
            <th scope="col">Precision</th>
            {isAgent && <th scope="col">Cost</th>}
            <th scope="col">Notes</th>
          </tr>
        </thead>
        <tbody>
          {(run.results ?? []).map((r) => (
            <tr key={r.case_id} className={r.false_verified ? "alarm" : ""}>
              <td>
                {r.solve.task_id ? <a href={`#/tasks/${r.solve.task_id}`}>{r.case_id}</a> : r.case_id}
                <div className="muted small">{r.category}</div>
              </td>
              <td>
                <span className={`mark ${r.success ? "good" : "bad"}`}>{r.success ? "Solved" : "Not solved"}</span>
              </td>
              {isAgent && (
                <td>
                  {r.solve.agent_status ?? "–"}
                  {r.false_verified && <div className="flag">claimed verified, hidden tests failed</div>}
                </td>
              )}
              <td>{pct(r.grade.patch_precision)}</td>
              {isAgent && <td>{money(r.solve.cost_usd)}</td>}
              <td className="muted small">
                {r.solve.error ?? r.grade.note}
                {r.grade.regressions.length > 0 && ` Regressions: ${r.grade.regressions.join(", ")}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import { useEffect, useState } from "react";
import { getHealth, type Health } from "./api";

// M0 shell: proves the browser -> FastAPI -> Postgres path and shows what is configured.
// The repository explorer (M1) replaces the body of this page.

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

function Pill({ good, label }: { good: boolean; label: string }) {
  return <span className={good ? "pill good" : "pill bad"}>{label}</span>;
}

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    getHealth(ctrl.signal)
      .then((health) => setState({ kind: "ok", health }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      });
    return () => ctrl.abort();
  }, []);

  return (
    <main className="shell">
      <header>
        <h1>verifix</h1>
        <p className="tag">Evidence-driven AI software engineer</p>
      </header>

      <section className="card">
        <h2>System</h2>
        {state.kind === "loading" && <p>Checking backend…</p>}
        {state.kind === "error" && (
          <p className="err">
            Backend unreachable ({state.message}). Is <code>uvicorn verifix.main:app --port 8010</code>{" "}
            running?
          </p>
        )}
        {state.kind === "ok" && (
          <dl>
            <dt>API</dt>
            <dd>
              <Pill good label={`v${state.health.version} · ${state.health.env}`} />
            </dd>
            <dt>Database</dt>
            <dd>
              <Pill good={state.health.database === "ok"} label={state.health.database} />
            </dd>
            <dt>Anthropic key</dt>
            <dd>
              <Pill good={state.health.anthropic === "configured"} label={state.health.anthropic} />
            </dd>
            <dt>Main model</dt>
            <dd>
              <code>{state.health.model_main}</code>
            </dd>
          </dl>
        )}
      </section>

      <section className="card muted">
        <h2>Next: M1 Repository Explorer</h2>
        <p>Register a local Git repository, browse its files, read source with line numbers, and search code.</p>
      </section>
    </main>
  );
}

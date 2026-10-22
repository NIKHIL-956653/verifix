import { useCallback, useEffect, useState } from "react";
import { api, type Health, type Repo } from "./api";
import { EvalsPage } from "./Evals";
import { ReposPage } from "./Repos";
import { TasksPage } from "./Tasks";

type Route = { page: "tasks" | "repos" | "evals"; id: string | null };

function parse(hash: string): Route {
  const [, page, id] = hash.replace(/^#/, "").split("/");
  if (page === "repos" || page === "evals") return { page, id: id ?? null };
  return { page: "tasks", id: id ?? null };
}

function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function SystemStatus({ health, error }: { health: Health | null; error: string | null }) {
  if (error)
    return (
      <span className="sys bad" title={error}>
        Backend unreachable — start <code>uvicorn verifix.main:app --port 8010</code>
      </span>
    );
  if (!health) return <span className="sys">Connecting…</span>;
  const problems = [
    health.database !== "ok" && "database down",
    health.anthropic !== "configured" && "no Anthropic key",
  ].filter(Boolean);
  return (
    <span className={problems.length ? "sys warn" : "sys good"} title={`v${health.version}`}>
      {problems.length ? problems.join(", ") : health.model_main}
    </span>
  );
}

export default function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);

  const loadRepos = useCallback(() => {
    api.repos().then(setRepos).catch(() => {});
  }, []);

  useEffect(() => {
    const check = () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setHealthError(null);
        })
        .catch((e) => setHealthError(e instanceof Error ? e.message : String(e)));
    check();
    loadRepos();
    const t = window.setInterval(check, 15000);
    return () => window.clearInterval(t);
  }, [loadRepos]);

  const nav = [
    { page: "tasks", label: "Tasks" },
    { page: "repos", label: "Repositories" },
    { page: "evals", label: "Benchmark" },
  ] as const;

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/tasks">
          verifix
        </a>
        <nav>
          {nav.map((n) => (
            <a
              key={n.page}
              href={`#/${n.page}`}
              className={route.page === n.page ? "on" : ""}
              aria-current={route.page === n.page ? "page" : undefined}
            >
              {n.label}
            </a>
          ))}
        </nav>
        <SystemStatus health={health} error={healthError} />
      </header>
      {route.page === "tasks" && <TasksPage taskId={route.id} repos={repos} />}
      {route.page === "repos" && <ReposPage repoId={route.id} repos={repos} reload={loadRepos} />}
      {route.page === "evals" && <EvalsPage runId={route.id} />}
    </div>
  );
}

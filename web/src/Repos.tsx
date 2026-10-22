import { useEffect, useMemo, useState } from "react";
import { api, type FileList, type FileRead, type Overview, type Repo, type SearchResult } from "./api";
import { errText, go, Notice } from "./ui";

export function ReposPage({
  repoId,
  repos,
  reload,
}: {
  repoId: string | null;
  repos: Repo[];
  reload: () => void;
}) {
  return (
    <div className="split">
      <aside className="rail">
        <AddRepo onAdded={(r) => { reload(); go(`/repos/${r.id}`); }} />
        {repos.length === 0 && <p className="empty">No repositories registered yet.</p>}
        <ul className="tasklist">
          {repos.map((r) => (
            <li key={r.id}>
              <a href={`#/repos/${r.id}`} className={r.id === repoId ? "current" : ""}>
                <span className="tl-title">{r.name}</span>
                <span className="tl-meta">{r.root_path}</span>
              </a>
            </li>
          ))}
        </ul>
      </aside>
      <section className="main">
        {repoId ? (
          <Explorer key={repoId} repo={repos.find((r) => r.id === repoId)} id={repoId} reload={reload} />
        ) : (
          <div className="blank">
            <h2>Your repositories</h2>
            <p>
              Register a local Git repository by its path, or import one from GitHub. verifix reads it
              through a jail that refuses secrets (.env, keys) and anything outside the repository.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function AddRepo({ onAdded }: { onAdded: (r: Repo) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isUrl = /^https:\/\/github\.com\//.test(value.trim());
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (isUrl && !window.confirm(`Clone ${value.trim()} to your machine and register it?`)) return;
    setBusy(true);
    try {
      const repo = isUrl ? (await api.importGithub(value.trim())).repo : await api.registerRepo(value.trim());
      setValue("");
      onAdded(repo);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="addrepo" onSubmit={submit}>
      <label className="field">
        <span>Local path or GitHub URL</span>
        <input
          required
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="G:\nickel_proj\my-app"
        />
      </label>
      <button type="submit" className="btn primary wide" disabled={busy}>
        {busy ? "Adding…" : isUrl ? "Clone and add" : "Add repository"}
      </button>
      {error && <Notice kind="error">{error}</Notice>}
    </form>
  );
}

function Explorer({ id, repo, reload }: { id: string; repo?: Repo; reload: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [files, setFiles] = useState<FileList | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<FileRead | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.overview(id), api.files(id)])
      .then(([o, f]) => {
        setOverview(o);
        setFiles(f);
      })
      .catch((e) => setError(errText(e)));
  }, [id]);

  const shown = useMemo(() => {
    const f = filter.toLowerCase();
    return (files?.files ?? []).filter((x) => x.path.toLowerCase().includes(f)).slice(0, 400);
  }, [files, filter]);

  const openFile = (path: string) =>
    api
      .readFile(id, path)
      .then(setOpen)
      .catch((e) => setError(errText(e)));

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    api
      .search(id, query)
      .then(setHits)
      .catch((err) => setError(errText(err)));
  };

  return (
    <div className="explorer">
      <header className="task-head">
        <div className="task-title">
          <h2>{repo?.name ?? "Repository"}</h2>
          <p className="request">{repo?.root_path}</p>
        </div>
        <div className="row">
          <button type="button" className="btn primary" onClick={() => go("/tasks/new")}>
            New task here
          </button>
          <button
            type="button"
            className="btn quiet"
            onClick={async () => {
              if (!window.confirm("Forget this repository and its tasks? Files on disk are not touched.")) return;
              await api.forgetRepo(id);
              reload();
              go("/repos");
            }}
          >
            Forget
          </button>
        </div>
      </header>
      {error && <Notice kind="error">{error}</Notice>}
      {overview && (
        <dl className="meters">
          <div>
            <dt>Files</dt>
            <dd>{overview.file_count}</dd>
          </div>
          <div>
            <dt>Languages</dt>
            <dd className="small">
              {Object.entries(overview.languages)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 3)
                .map(([k]) => k)
                .join(", ") || "–"}
            </dd>
          </div>
          <div>
            <dt>Tests</dt>
            <dd className="small">{overview.test_runner ?? "none found"}</dd>
          </div>
          <div>
            <dt>Frameworks</dt>
            <dd className="small">{overview.frameworks.join(", ") || "–"}</dd>
          </div>
        </dl>
      )}

      <form className="row search" onSubmit={search}>
        <input
          aria-label="Search code"
          placeholder="Search code"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="btn">
          Search
        </button>
      </form>
      {hits && (
        <ul className="hits">
          {hits.hits.length === 0 && <li className="empty">No matches.</li>}
          {hits.hits.map((h, i) => (
            <li key={i}>
              <button type="button" className="linkish" onClick={() => openFile(h.path)}>
                {h.path}:{h.line}
              </button>
              <code>{h.text}</code>
            </li>
          ))}
          {hits.truncated && <li className="muted">More results were cut off; narrow the search.</li>}
        </ul>
      )}

      <div className="browse">
        <div className="files">
          <input
            aria-label="Filter files"
            placeholder="Filter files"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <ul>
            {shown.map((f) => (
              <li key={f.path}>
                <button
                  type="button"
                  className={open?.path === f.path ? "file on" : "file"}
                  onClick={() => openFile(f.path)}
                >
                  {f.path}
                </button>
              </li>
            ))}
          </ul>
          {files && files.hidden > 0 && (
            <p className="muted">{files.hidden} files withheld (secrets or outside the repository).</p>
          )}
        </div>
        <div className="viewer">
          {open ? (
            <>
              <p className="muted">
                {open.path} · lines {open.start}–{open.end} of {open.total_lines}
                {open.truncated && " (truncated)"}
              </p>
              <pre className="code">{open.content}</pre>
            </>
          ) : (
            <p className="empty">Choose a file to read it.</p>
          )}
        </div>
      </div>
    </div>
  );
}

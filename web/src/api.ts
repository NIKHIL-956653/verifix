// Typed client for the verifix API. Every call goes through /api (proxied to FastAPI in dev),
// so no backend address or key ever lives in frontend code.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail))
        detail = body.detail.map((d: { msg?: string }) => d.msg ?? "").join("; ");
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  req<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

// ------------------------------------------------------------------ types

export type Health = {
  status: "ok" | "degraded";
  version: string;
  env: string;
  database: "ok" | "down";
  anthropic: "configured" | "missing";
  model_main: string;
};

export type Repo = {
  id: string;
  name: string;
  root_path: string;
  default_branch: string | null;
  created_at: string;
};

export type Overview = {
  languages: Record<string, number>;
  frameworks: string[];
  test_runner: string | null;
  manifests: string[];
  entry_points: string[];
  has_dockerfile: boolean;
  file_count: number;
  test_dirs: string[];
};

export type FileList = { files: { path: string; size: number }[]; hidden: number; truncated: boolean };
export type FileRead = {
  path: string;
  start: number;
  end: number;
  total_lines: number;
  content: string;
  truncated: boolean;
};
export type SearchResult = {
  hits: { path: string; line: number; text: string }[];
  files_scanned: number;
  truncated: boolean;
  timed_out: boolean;
};

export type TaskKind = "ask" | "investigate" | "fix" | "review";
export type Mode = "ask" | "auto";
export type Policy = { approve_plan: boolean; write: Mode; execute: Mode; install: Mode };

export type Claim = { statement: string; evidence: number[]; supported: boolean; missing: number[] };
export type Report = {
  outcome?: string;
  claimed_outcome?: string;
  summary?: string;
  root_cause?: string | null;
  claims?: Claim[];
  evidence_coverage?: number;
  uncertainty?: string;
  next_steps?: string[];
  notes?: string[];
  changed_files?: string[];
  verification?: {
    test_runs: number;
    failing_runs: number;
    reproduced_before_fix: boolean;
    last_run_after_last_change: { evidence: number; status: string; passed?: number; failed?: number } | null;
  };
  pull_request?: { url: string; number: number; branch: string; repo: string; draft: boolean };
};

export type Task = {
  id: string;
  repo_id: string;
  kind: TaskKind;
  title: string;
  request: string;
  status: string;
  error: string | null;
  policy: Policy;
  budget: Record<string, number>;
  plan: {
    objective?: string;
    hypotheses?: { statement: string; how_to_check: string }[];
    steps?: string[];
    acceptance_criteria?: string[];
    risks?: string[];
  } | null;
  report: Report | null;
  worktree_path: string | null;
  worktree_branch: string | null;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  active_seconds: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pending_approval: { id: string; action: string; preview: string; payload: Record<string, unknown> } | null;
};

export type Step = {
  seq: number;
  kind: "model_call" | "tool_call" | "approval" | "status" | "error";
  name: string | null;
  input: Record<string, unknown>;
  summary: string;
  ok: boolean;
  evidence: number | null;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  at: string | null;
};

export type EvidenceItem = {
  seq: number;
  kind: string;
  summary: string;
  meta: Record<string, unknown>;
  sha256: string;
  content?: string;
};

export type Finding = {
  id: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  category: string;
  path: string;
  line_start: number;
  line_end: number;
  title: string;
  explanation: string;
  recommendation: string;
  evidence: number[];
  confidence: "confirmed" | "plausible";
  note: string | null;
};

export type BenchCase = {
  id: string;
  title: string;
  category: string;
  request: string;
  expected_files: string[];
};

export type EvalSummary = {
  state?: "running" | "finished" | "failed";
  error?: string;
  cases?: number;
  done?: number;
  successes?: number;
  success_rate?: number;
  patch_applies_rate?: number;
  cases_with_regressions?: number;
  mean_patch_precision?: number | null;
  mean_evidence_coverage?: number | null;
  tool_reliability?: number | null;
  claimed_verified?: number;
  false_verified?: number;
  verified_precision?: number | null;
  total_cost_usd?: number;
  mean_duration_s?: number;
};

export type EvalCaseResult = {
  case_id: string;
  category: string;
  success: boolean;
  false_verified: boolean;
  grade: {
    patch_applies: boolean;
    hidden_passed: boolean;
    regressions: string[];
    changed_files: string[];
    patch_precision: number | null;
    note: string;
  };
  solve: {
    agent_status: string | null;
    evidence_coverage: number | null;
    tool_calls: number;
    tool_errors: number;
    cost_usd: number;
    duration_s: number;
    task_id: string | null;
    error: string | null;
  };
};

export type EvalRun = {
  id: string;
  name: string;
  mode: string;
  config: { solver?: string; cases?: string[] };
  summary: EvalSummary;
  created_at: string;
  results: EvalCaseResult[] | null;
};

// ------------------------------------------------------------------ calls

export const api = {
  health: (signal?: AbortSignal) => req<Health>("/health", { signal }),

  repos: () => req<Repo[]>("/repos"),
  registerRepo: (path: string) => post<Repo>("/repos", { path }),
  forgetRepo: (id: string) => req<void>(`/repos/${id}`, { method: "DELETE" }),
  overview: (id: string) => req<Overview>(`/repos/${id}/overview`),
  files: (id: string) => req<FileList>(`/repos/${id}/files`),
  readFile: (id: string, path: string) =>
    req<FileRead>(`/repos/${id}/file?path=${encodeURIComponent(path)}`),
  search: (id: string, q: string) =>
    req<SearchResult>(`/repos/${id}/search?q=${encodeURIComponent(q)}`),
  importGithub: (url: string) =>
    post<{ repo: Repo; cloned: boolean }>("/github/import", { url, confirm: true }),
  githubStatus: () => req<{ token_configured: boolean }>("/github/status"),

  tasks: (repoId?: string) => req<Task[]>(`/tasks${repoId ? `?repo_id=${repoId}` : ""}`),
  task: (id: string) => req<Task>(`/tasks/${id}`),
  createTask: (body: {
    repo_id: string;
    kind: TaskKind;
    request: string;
    policy: Policy;
    budget?: Record<string, number>;
  }) => post<Task>("/tasks", body),
  steps: (id: string, after = 0) => req<Step[]>(`/tasks/${id}/steps?after=${after}`),
  evidence: (id: string) => req<EvidenceItem[]>(`/tasks/${id}/evidence`),
  evidenceItem: (id: string, seq: number) => req<EvidenceItem>(`/tasks/${id}/evidence/${seq}`),
  findings: (id: string) => req<Finding[]>(`/tasks/${id}/findings`),
  diff: (id: string) =>
    req<{ diff: string; files: string[]; stats: { files: number; added: number; removed: number } }>(
      `/tasks/${id}/diff`,
    ),
  decide: (id: string, approvalId: string, approve: boolean, reason?: string) =>
    post<Task>(`/tasks/${id}/approvals/${approvalId}`, { approve, reason: reason || null }),
  cancel: (id: string) => post<Task>(`/tasks/${id}/cancel`, {}),
  retry: (id: string) => post<Task>(`/tasks/${id}/retry`, {}),
  apply: (id: string) =>
    post<{ applied: boolean; files: string[]; conflicts: string[]; message: string }>(
      `/tasks/${id}/apply`,
      { confirm: true },
    ),
  removeWorkspace: (id: string) => req<void>(`/tasks/${id}/workspace`, { method: "DELETE" }),
  openPr: (id: string) =>
    post<{ url: string; number: number; branch: string }>(`/tasks/${id}/pull-request`, {
      confirm: true,
    }),
  prChecks: (id: string) =>
    req<{ overall: string; checks: { name: string; status: string; conclusion: string | null; url: string }[] }>(
      `/tasks/${id}/pull-request/checks`,
    ),

  benchCases: () => req<BenchCase[]>("/evals/cases"),
  evalRuns: () => req<EvalRun[]>("/evals"),
  evalRun: (id: string) => req<EvalRun>(`/evals/${id}`),
  startEval: (solver: "reference" | "noop" | "agent", confirm_cost = false) =>
    post<EvalRun>("/evals", { solver, confirm_cost }),
};

export const TERMINAL = new Set([
  "completed",
  "fixed_unverified",
  "verified",
  "failed",
  "cancelled",
  "budget_exhausted",
]);

import { createContext, useContext, type ReactNode } from "react";

// ------------------------------------------------------------------ routing (hash based)

export function go(path: string) {
  window.location.hash = path;
}

// ------------------------------------------------------------------ evidence chips

// Any component can open an evidence item in the side sheet.
export const EvidenceCtx = createContext<(seq: number) => void>(() => {});

export function EChip({ seq, missing = false }: { seq: number; missing?: boolean }) {
  const open = useContext(EvidenceCtx);
  if (missing)
    return (
      <span className="echip missing" title="cited evidence that does not exist">
        E{seq}?
      </span>
    );
  return (
    <button type="button" className="echip" onClick={() => open(seq)} title={`Open evidence E${seq}`}>
      E{seq}
    </button>
  );
}

/** Text in which "[E12]" references become clickable chips. */
export function WithChips({ text }: { text: string }) {
  const parts = text.split(/\[E(\d+)\]/g);
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <EChip key={i} seq={Number(p)} /> : <span key={i}>{p}</span>))}
    </>
  );
}

// ------------------------------------------------------------------ verdict

const VERDICT: Record<string, { label: string; tone: string; note: string }> = {
  verified: { label: "Verified", tone: "good", note: "A passing test run followed the last change." },
  fixed_unverified: {
    label: "Changed, not verified",
    tone: "warn",
    note: "No passing test run after the last change.",
  },
  completed: { label: "Answered", tone: "plain", note: "Read-only task finished." },
  failed: { label: "Failed", tone: "bad", note: "The task stopped with an error." },
  cancelled: { label: "Cancelled", tone: "plain", note: "Stopped by you." },
  budget_exhausted: { label: "Out of budget", tone: "warn", note: "A budget limit stopped the task." },
  awaiting_approval: { label: "Waiting for you", tone: "ask", note: "An action needs your decision." },
  running: { label: "Working", tone: "live", note: "" },
  planning: { label: "Planning", tone: "live", note: "" },
  queued: { label: "Queued", tone: "live", note: "" },
};

export function statusLabel(s: string) {
  return VERDICT[s]?.label ?? s;
}

export function StatusDot({ status }: { status: string }) {
  return <span className={`dot ${VERDICT[status]?.tone ?? "plain"}`} aria-hidden />;
}

export function Verdict({ status }: { status: string }) {
  const v = VERDICT[status] ?? { label: status, tone: "plain", note: "" };
  return (
    <div className={`verdict ${v.tone}`} role="status">
      <span className="verdict-label">{v.label}</span>
      {v.note && <span className="verdict-note">{v.note}</span>}
    </div>
  );
}

// ------------------------------------------------------------------ diff

export function Diff({ text }: { text: string }) {
  if (!text.trim()) return <p className="empty">No changes.</p>;
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <pre className="diff" tabIndex={0}>
      {lines.map((l, i) => {
        const cls = l.startsWith("diff --git")
          ? "d-file"
          : l.startsWith("@@")
            ? "d-hunk"
            : l.startsWith("+++") || l.startsWith("---")
              ? "d-meta"
              : l.startsWith("+")
                ? "d-add"
                : l.startsWith("-")
                  ? "d-del"
                  : "";
        return (
          <span key={i} className={cls}>
            {l}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

// ------------------------------------------------------------------ bits

export function Notice({ kind = "info", children }: { kind?: "info" | "error"; children: ReactNode }) {
  return <div className={`notice ${kind}`}>{children}</div>;
}

export function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`;
}

export function money(v: number | null | undefined) {
  return v === null || v === undefined ? "–" : `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`;
}

export function ago(iso: string | null) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

// Typed client for the verifix API. Every call goes through /api (proxied in dev).

export type Health = {
  status: "ok" | "degraded";
  version: string;
  env: string;
  database: "ok" | "down";
  anthropic: "configured" | "missing";
  model_main: string;
};

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch("/api/health", { signal });
  if (!res.ok) throw new Error(`health check failed: HTTP ${res.status}`);
  return (await res.json()) as Health;
}

# API

Base URL in development: `http://localhost:8010`. The web UI calls it through the Vite
proxy at `/api`. Interactive docs: `http://localhost:8010/docs`.

## GET /health

Is the service usable? Always HTTP 200. Read `status` for the verdict.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "env": "dev",
  "database": "ok",
  "anthropic": "configured",
  "model_main": "claude-sonnet-5-5"
}
```

| Field | Values |
|---|---|
| `status` | `ok` when the database answers and a model key is configured, else `degraded` |
| `database` | `ok` / `down` |
| `anthropic` | `configured` / `missing`. Never the key, its length or its prefix |

## Planned

| Milestone | Endpoints |
|---|---|
| M1 | `POST /repos`, `GET /repos`, `GET /repos/{id}/files`, `GET /repos/{id}/file?path&start&end`, `GET /repos/{id}/search?q`, `GET /repos/{id}/git/status` |
| M2–M3 | `POST /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/evidence`, `POST /tasks/{id}/cancel` |
| M4 | `GET /tasks/{id}/patch`, `POST /tasks/{id}/approvals` |

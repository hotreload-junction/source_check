# contracts/

The interface between `backend/` and `frontend/`. Shared, owned by everyone. Change rules: AGENTS.md section 4.

- `api.md`: endpoints, SSE events, all types and enums (v1). **Authoritative.**
- `examples/`: fictional mock payloads (`example.*` domains only). The frontend codes against these until
  the backend lands; backend tests can assert their output validates against the same shapes.
  - `analysis_done.json`, `analysis_running.json`: `GET /api/analyses/{id}`
  - `analysis_attributed.json`: attributed claim with its found evidence (stub depth 1 -> found page depth 2), see api.md
  - `events.sse`: sample SSE stream
  - `demo.json`: `GET /api/demo`
  - `methodology.json`: `GET /api/methodology` (placeholder numbers)
- `../.env.example`: all env keys with defaults (secrets empty).

The backend's Pydantic models mirror `api.md`. FastAPI's generated `/openapi.json` is a convenience, not
the contract: if they disagree, fix the code or ship a contract change.

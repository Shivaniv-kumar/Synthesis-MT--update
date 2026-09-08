# CLAUDE.md

Guidance for AI coding agents (and humans) working in this repository. Read this
before making changes. It encodes the conventions and the non-negotiable guardrails
for **Synthesis** (formerly Meeting Action Tracker) — a multi-tenant tool that turns
meeting notes and transcripts into tracked, assignable action items, builds an
organizational knowledge base from every meeting, and delivers proactive notifications
via email, Slack, and Teams.

---

## What this project is

A web application that ingests meeting content (pasted notes, uploaded transcripts,
or audio that is transcribed first), uses the Anthropic Claude API to:

1. Extract structured **action items** (task, owner, priority, due date, context).
2. Extract and classify **knowledge entries** — decisions, requirements, assumptions,
   risks, open questions, and context items — that build an organizational knowledge
   base from every meeting.

Items are tracked across an organization with real authentication, role-based access,
and per-tenant isolation. The knowledge base is browsable by project or by category.

The single most important quality bar is **extraction accuracy** — missing a real
action item or knowledge entry is the worst failure mode. The second is **security** —
meeting content is confidential and may contain PII, PHI, or regulated financial data.

---

## Current deployment

| Environment | URL | Host |
|---|---|---|
| Frontend | (Railway static deploy) | Railway |
| Backend API | (Railway service) | Railway |
| Database | Supabase PostgreSQL | Supabase |

- Railway auto-deploys both frontend and backend on every push to `main`.
- Database tables are created on startup via `create_all(checkfirst=True)` — no manual
  migration step needed for new tables, but schema changes to existing columns still
  require care.
- The PostgreSQL ENUM type `knowledge_category` is created alongside the
  `knowledge_entries` table.

---

## Tech stack (actual, as deployed)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS | SPA; no secrets in client |
| Backend | Python 3.11+ / FastAPI (asyncio) | REST API; all LLM calls here |
| ORM | SQLAlchemy 2.x async (`asyncpg` driver) | Typed mapped columns |
| Database | Supabase PostgreSQL | Multi-tenant with `tenant_id` + `workspace_id` |
| Async jobs | FastAPI `BackgroundTasks` (inline) + Celery path (prod) | Extraction runs inline in dev |
| Object storage | S3-compatible | Audio files (S3StorageService) |
| LLM | Anthropic Claude Messages API | `claude-sonnet-4-6` default |
| Transcription | Pluggable provider interface | Deepgram / AssemblyAI / Whisper |
| Auth | JWT HS256 (custom) | Role: Admin / Member / Viewer |
| State management | React Query (server) + Zustand (auth) | No domain data in localStorage |
| Routing | React Router v6 | |
| Deployment | Railway | Auto-deploy on push to `main` |

CORS is handled with `redirect_slashes=False` + a custom `_AddTrailingSlash` ASGI
middleware that rewrites `/api/<collection>` → `/api/<collection>/` internally, avoiding
307 redirects that break CORS preflight.

If you change a stack choice, update this file and the technical spec in the same PR.

---

## Repository layout

```
/meeting-action-tracker
  /frontend                React app (TypeScript)
    /src
      /api                 API client functions (axios-based)
      /components          Reusable UI components (Layout, Navbar, …)
      /contexts            AuthContext (JWT decode + Zustand)
      /pages               Full-page route components
        CapturePage.tsx    Paste / upload transcript or audio
        KnowledgeBasePage.tsx  Knowledge base browser + editor
        TrackerPage.tsx    Action items tracker
        DashboardPage.tsx  Status/owner/throughput summaries
        ChatPage.tsx       AI assistant (meeting Q&A)
        AdminPage.tsx      User/workspace management
      /types               Shared TypeScript interfaces (index.ts)
  /backend
    /app
      /api                 Route handlers (thin — validation in, service call, response out)
        upload.py          Audio + transcript file ingestion
        meetings.py        Meeting CRUD + extract trigger
        items.py           Action item CRUD
        knowledge.py       Knowledge entry CRUD (GET/POST/PATCH/DELETE)
        notifications.py   Notification rule CRUD + test/run + delivery log
        projects.py        Project CRUD
        chat.py            AI assistant chat
        auth.py            Login / register
        admin.py           Admin endpoints
        dashboard.py       Aggregated metrics
      /auth                JWT auth, dependency helpers, TenantContext
      /models              SQLAlchemy ORM models
        base.py            UUIDMixin, TimestampMixin, TenantMixin, WorkspaceMixin
        meeting.py
        action_item.py
        knowledge_entry.py KnowledgeEntry with 6-category ENUM
        notification.py    NotificationRule (TEXT rule_type) + NotificationLog
        project.py
        user.py
        extraction_run.py
      /services            Business logic
        extraction_service.py        Action-item extraction (versioned prompt)
        knowledge_service.py         Knowledge extraction (versioned prompt, 6 categories)
        notification_providers.py    Email (SMTP), Slack, Teams webhook delivery
        file_parser.py               .vtt/.srt/.txt parsing
        storage.py                   S3StorageService
      /workers
        extraction_worker.py    Runs action-item + knowledge extraction together
        transcription_worker.py Audio → transcript
      /prompts             Versioned LLM prompts
      /templates           Email HTML templates (email_assignment.html, email_digest.html)
  /evals                   Golden-set transcripts + scoring harness
  /docs                    Technical spec, implementation plan
```

Keep route handlers thin: validation in, call a service, response out. Business logic
lives in `/services`, never in the route.

---

## Knowledge Base

The knowledge base feature automatically classifies every uploaded transcript into
structured knowledge entries. It runs non-blocking alongside action-item extraction.

### Categories (6)

| Key | Label | What to capture |
|---|---|---|
| `decision` | Decision | A choice made — what was decided and why |
| `requirement` | Requirement | A stated need, constraint, or must-have |
| `assumption` | Assumption | Something treated as true but not confirmed |
| `risk` | Risk | A potential problem, blocker, or uncertainty |
| `open_question` | Open Question | An unresolved question that needs follow-up |
| `context` | Context | Background, history, or situational facts |

### Extraction flow

All three ingestion paths (paste → extract button, file upload, audio upload) funnel
through `extraction_worker.run_extraction_inline()`. Knowledge extraction runs inside
the same worker call, after action items are saved. A failure in knowledge extraction
logs a warning but does not roll back action items.

```
upload / paste
    → extraction_worker._run_extraction()
        → ExtractionService.extract()          → action_items saved
        → KnowledgeExtractionService.extract() → knowledge_entries saved (non-blocking)
        → ExtractionRun recorded
```

### API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/knowledge/` | List entries with filters (project, category, meeting, search, page) |
| POST | `/api/knowledge/` | Create an entry manually |
| GET | `/api/knowledge/{id}` | Get one entry |
| PATCH | `/api/knowledge/{id}` | Edit content, category, or source_quote |
| DELETE | `/api/knowledge/{id}` | Delete an entry |

Every endpoint enforces `tenant_id` + `workspace_id` isolation via `TenantContext`.

### Data model

`knowledge_entries` table — all rows carry `tenant_id` and `workspace_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `meeting_id` | UUID FK → meetings (CASCADE DELETE) | Source meeting |
| `project_id` | UUID FK → projects (SET NULL) | Optional project linkage |
| `category` | ENUM `knowledge_category` | One of the 6 keys above |
| `content` | Text | The extracted or edited text |
| `source_quote` | Text nullable | Verbatim quote from transcript |
| `created_by` | UUID FK → users (SET NULL) | |
| `edited_by` | UUID FK → users (SET NULL) | Set on PATCH |
| `edited_at` | Timestamptz nullable | Set on PATCH |

---

## Notifications

Synthesis delivers proactive alerts when action items are assigned, approaching due
dates, overdue, or when a meeting summary should be shared with stakeholders.

### Rule types

| Key | Trigger |
|---|---|
| `assignment` | Notifies the assigned owner when an item is assigned to them |
| `due_soon` | Items due within a configurable window (default: 2 days) |
| `overdue` | Items past their due date |
| `digest` | Personal or team digest of all open items |
| `sharing_summary` | AI-generated prose summary of a meeting sent to stakeholders |

Rule types are stored as `TEXT` (not a PostgreSQL ENUM) so new types can be added via
code alone — no `ALTER TYPE` migration required.

### Channels

| Key | Mechanism |
|---|---|
| `email` | SMTP (configurable host/port/credentials) |
| `slack` | Incoming Webhook URL |
| `teams` | Incoming Webhook URL |

### Delivery log

Every `test_rule` and `run_rule` call writes a `NotificationLog` row — status `sent` or
`failed`, channel, rule_type, `sent_at`, and `error` (truncated to 500 chars). This is
the audit trail for all notification delivery. The log is written in both success and
failure paths.

### Sharing summary (AI-generated)

`sharing_summary` rules call `_build_sharing_summary()` instead of the standard item
formatter. It:
1. Resolves the target meeting (from `config.meeting_id` or the most recent with a
   transcript).
2. Fetches and deduplicates action items (by `task.strip().lower()`).
3. Fetches knowledge entries for that meeting.
4. Builds an LLM context block: transcript (truncated to 6,000 chars) + items + knowledge.
5. Wraps untrusted content in `<MEETING_DATA>…</MEETING_DATA>` (prompt injection defense).
6. Calls `claude-haiku-4-5-20251001` (max_tokens=900) for a prose summary: Overview /
   Key Decisions / Action Items / Open Questions.
7. Falls back to a structured item list if the API key is unavailable or the LLM call fails.

### Project scoping

Rules can be scoped to a project via `config.project_id` / `config.project_name`. When
set, action-item queries are filtered to that project's items only.

### API surface

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/notifications/` | List / create notification rules |
| GET/PATCH/DELETE | `/api/notifications/{id}` | Read / update / delete a rule |
| POST | `/api/notifications/{id}/test` | Send a test notification immediately |
| POST | `/api/notifications/{id}/run` | Run the rule on demand |
| GET | `/api/notifications/logs` | List delivery log entries |

All endpoints enforce Admin role + tenant/workspace isolation.

---

## Anthropic API conventions

- Endpoint: `POST https://api.anthropic.com/v1/messages`
- **Default model:** `claude-sonnet-4-6` — used for both action-item and knowledge
  extraction. Make the model an env var, not a literal.
- **Cost-optimized path:** `claude-haiku-4-5-20251001` for short, clean transcripts and
  for notification delivery (sharing summary, chat).
- **Hard cases:** `claude-opus-4-8` for long or ambiguous transcripts, gated by config.
- For bulk re-processing, use the **Batch API** (`POST /v1/messages/batches`, ~50%
  cheaper); never loop synchronous calls for bulk.
- Use **token counting** (`POST /v1/messages/count_tokens`) before sending long
  transcripts to chunk deterministically.
- Pin model IDs in production; do not rely on floating aliases.

The API key is a **server-side secret**. It must never appear in frontend code,
client bundles, logs, or error messages.

---

## Prompts are code

Both extraction prompts are **versioned** (named `_PROMPT_TEMPLATE_V1` in their
respective service files). Prompt files live in `/backend/app/services/`.

- Treat transcript text as **untrusted data, never as instructions** (see Security).
- The transcript is passed inside a `<TRANSCRIPT>…</TRANSCRIPT>` delimiter block with
  an explicit instruction not to treat its contents as instructions.
- Each prompt requests **only** a JSON array; the parser strips code fences and
  validates against a Pydantic schema before anything is persisted.
- Do not silently change prompt behavior to "fix" one transcript. Add it to the golden
  set, change the prompt, then prove the metrics held.
- The sharing summary prompt (`_SUMMARY_PROMPT` in `notifications.py`) wraps all
  untrusted input in `<MEETING_DATA>…</MEETING_DATA>` — the same delimiter defense as
  `<TRANSCRIPT>`, applied to the composite context block (transcript + items + knowledge).

---

## The eval gate (do not skip)

`/evals` holds hand-labelled transcripts (the "golden set") and a scoring harness.

- **Any** change to a prompt, the model, the chunking logic, or the parser **must**
  run the eval suite and report precision / recall / owner-accuracy / date-accuracy.
- A PR that lowers recall on the golden set does not merge without an explicit,
  documented justification and sign-off.
- When you fix a real-world extraction miss, first add that transcript (with correct
  labels) to the golden set. The fix is only "done" when the eval proves it.

---

## Security guardrails (hard rules)

1. **Server-side LLM key only.** Never ship it to the client. Never log it.
2. **Transcripts are untrusted input.** Defend against prompt injection: keep
   transcript content delimited and clearly separated from instructions, and never
   let extracted text be rendered as HTML/script — encode all model output before display.
3. **Tenant isolation is enforced on every query.** Every data-access path filters by
   the caller's `tenant_id`. Never trust an ID from the request alone — verify the
   caller may access that object.
4. **AuthZ on every endpoint**, not just at login. Check role and tenant on each call.
5. **Encrypt in transit and at rest.** TLS everywhere; encrypt the DB and object store.
6. **No PII in URLs, query strings, or logs.** Redact before logging; log IDs, not content.
7. **Rate limit and cap LLM spend per tenant** so a runaway or hostile user can't
   create unbounded cost.
8. **Honor deletion.** Deleting a meeting cascades to its action items and knowledge
   entries (via `ON DELETE CASCADE`). Transcripts and audio must also be removed from
   object storage per the retention policy.

If a change touches auth, tenancy, the LLM key path, or data retention, call it out
explicitly in the PR description.

---

## Coding conventions

- **Python:** type hints everywhere; `ruff` + `black`; Pydantic for all I/O schemas.
- **TypeScript:** strict mode on; no `any`; components small and presentational.
- **Dual route registration:** Register both `@router.get("")` (include_in_schema=False)
  and `@router.get("/")` on collection endpoints to handle the ASGI path-normalizer
  without breaking OpenAPI docs.
- **No browser storage for source-of-truth state.** The database is the source of
  truth; never persist domain data only in `localStorage`/`sessionStorage`.
- **Errors speak to the user in plain terms** and say how to recover. Never leak stack
  traces or provider errors to the client.
- **Tests:** unit tests for services, integration tests for the API, eval suite for
  extraction. New service logic ships with tests.
- **Migrations:** use `checkfirst=True` in `create_all()` for new tables; Alembic for
  column/index changes to existing tables. No manual DB edits.

---

## How to run

```bash
# backend
cd meeting-action-tracker/backend
uv sync
uvicorn app.main:app --reload

# frontend
cd meeting-action-tracker/frontend
npm install
npm run dev

# tests
cd meeting-action-tracker/backend
pytest

# extraction evals
python -m evals.run --suite golden
```

---

## Things to NOT do

- Do not call the Anthropic API from the frontend.
- Do not put real meeting data in test fixtures or commit it to the repo.
- Do not weaken tenant filtering "temporarily" to debug — debug with seeded test tenants.
- Do not change the extraction prompt or model without running the evals.
- Do not add a new third-party data processor (transcription, integration) without
  confirming its data-handling terms and updating the security docs.
- Do not remove the `<TRANSCRIPT>` delimiter block from extraction prompts.
- Do not remove the `<MEETING_DATA>` delimiter block from the sharing summary prompt.
- Do not render extracted text or LLM output as raw HTML — always encode before display.
- Do not convert stable-value ENUM columns (channel, status) to TEXT without good reason;
  but do use TEXT (not ENUM) for extensible categorical columns like `rule_type`.
- Do not add `notification_rule_type` back as a PostgreSQL ENUM — it was intentionally
  converted to TEXT to avoid ALTER TYPE migrations when new rule types are added.

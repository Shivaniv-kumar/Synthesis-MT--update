# Implementation Plan — Synthesis

A phased record of what has been built and the roadmap ahead. Completed phases are
documented with what shipped and what changed from the original plan. Future phases
list goals, key tasks, and exit criteria.

**Last updated:** June 2026

---

## Completed phases

---

### Phase 0 — Foundations ✓

**Goal:** A skeleton you can build on safely.

**Shipped:**
- Repo scaffolded: `/frontend` (React/TS/Vite/Tailwind), `/backend` (FastAPI),
  `/docs`, `/evals` skeleton.
- FastAPI + Supabase PostgreSQL + React running end-to-end locally and on Railway.
- Anthropic Claude API call moved server-side; key read from environment variable only.
- SQLAlchemy async (asyncpg) ORM with `UUIDMixin`, `TimestampMixin`, `TenantMixin`,
  `WorkspaceMixin` base mixins.
- Tables created at startup via `create_all(checkfirst=True)` — works in dev and
  production without a manual migration step for new tables.

**Deviations from plan:**
- No Celery/Redis in dev; using FastAPI `BackgroundTasks` for async extraction.
- Eval harness scaffolded but golden set not yet populated.

---

### Phase 1 — MVP core (single team, text in) ✓

**Goal:** A genuinely useful tool for one team — paste notes → accurate items →
trackable list, behind real login.

**Shipped:**
- **Auth:** email/password login; JWT HS256 access tokens; Member / Admin / Viewer
  roles; `get_current_user` + `get_tenant_context` FastAPI dependencies on every route.
- **Data model:** `meeting`, `action_item`, `user`, `workspace`, `extraction_run`,
  `audit_log` with `(tenant_id, workspace_id)` scoping.
- **Extraction service:** versioned prompt (`_PROMPT_TEMPLATE_V1`), strict JSON
  contract, Pydantic validation, confidence + `needs_review`, deterministic chunking
  (12,000-char chunks, 500-char overlap, 500,000-char hard cap).
- **Capture flow:** paste transcript → extract → review-and-edit → save. Three
  ingestion paths: paste, `.vtt`/`.srt`/`.txt` upload, audio upload.
- **Tracker:** list with owner/status/priority filters, status workflow, edit, delete.
- **Dashboard:** status counts and open-workload-by-owner.
- **CORS fix:** `redirect_slashes=False` + `_AddTrailingSlash` ASGI middleware
  (eliminates CORS-breaking 307 redirects from Railway's proxy layer).

**Deviations from plan:**
- Auth is custom JWT, not SSO — SSO deferred to a later phase.
- `needs_review` flag implemented but review queue UI not yet built.

---

### Phase 2 — Projects, chat assistant, admin ✓

**Goal:** Organize work across projects; give users an AI assistant for meeting context.

**Shipped:**
- **Projects:** full CRUD (`/api/projects/`); meetings and knowledge entries linked to
  a project (`project_id` FK); filter action items and knowledge by project.
- **AI chat assistant:** `/api/chat/` endpoint; Claude API answers questions about
  meeting content; `ChatPage.tsx` with message history.
- **Admin panel:** `AdminPage.tsx` + `/api/admin/` routes; Admin role can manage
  users, roles, and workspace settings.
- **Settings page:** `SettingsPage.tsx` for workspace-level configuration.
- **Navigation:** collapsible sidebar with Capture, Tracker, Dashboard, Assistant,
  Knowledge, and Admin/Settings (Admin only) items.
- **Multi-tenant isolation:** every query verified against `TenantContext`; object IDs
  verified against caller's tenant before access.

---

### Phase 3 — Knowledge Base ✓

**Goal:** Every uploaded transcript is automatically classified into a persistent,
browsable, editable knowledge base covering decisions, requirements, assumptions,
risks, open questions, and context.

**Shipped:**

**Backend:**
- `KnowledgeEntry` ORM model (`backend/app/models/knowledge_entry.py`) with 6-value
  PostgreSQL ENUM `knowledge_category`.
- FKs: `meeting_id` (CASCADE DELETE), `project_id` (SET NULL), `created_by`/`edited_by`
  (SET NULL on users).
- `KnowledgeExtractionService` (`backend/app/services/knowledge_service.py`):
  versioned prompt, same chunking as action-item extraction, Pydantic validation,
  category whitelist, content deduplication.
- `/api/knowledge/` router (GET list, POST, GET one, PATCH, DELETE); paginated list
  with filters: `project_id`, `category[]`, `meeting_id`, `search` (ilike), `page`,
  `page_size`. Each item enriched with `meeting_title` + `project_name`.
- `extraction_worker.py` updated: after action items are committed, knowledge
  extraction runs in a try/except block — failure logs a warning but does not roll
  back action items.

**Frontend:**
- `KnowledgeEntry`, `KnowledgeCategory`, `KnowledgeFilters` TypeScript interfaces
  (`frontend/src/types/index.ts`).
- `frontend/src/api/knowledge.ts`: `listKnowledge`, `createKnowledgeEntry`,
  `getKnowledgeEntry`, `updateKnowledgeEntry`, `deleteKnowledgeEntry`.
- `KnowledgeBasePage.tsx`: React Query + useMutation; view-by toggle (category /
  project); filter panel; search; collapsible `GroupSection` with entry count badge;
  `EntryCard` with read/edit modes (inline textarea, category select, source_quote
  textarea); category color coding.
- Knowledge nav item (book icon) added to `Layout.tsx`.
- `/knowledge` route added to `App.tsx`.

**Automatic extraction:**
All three ingestion paths (paste → Extract button, file upload, audio upload) route
through `run_extraction_inline()` in `extraction_worker.py`. Knowledge extraction is
automatic for every transcript — no user action required.

**Exit criteria met:**
- Knowledge entries auto-created on every transcript upload.
- Browsable by category or project, filterable, searchable.
- Entries editable (category, content, source_quote) and deletable in-place.
- Deletion of the source meeting cascades and removes knowledge entries.

---

### Phase 4 — Notifications and rebrand ✓

**Goal:** Alert the right people at the right time via email, Slack, and Teams;
generate AI-powered meeting summaries for sharing; and rebrand the product to Synthesis.

**Shipped:**

**Notification system — backend:**
- `NotificationRule` ORM model (`backend/app/models/notification.py`): `rule_type`
  stored as `TEXT` (`String(50)`) — **not** a PostgreSQL ENUM — so new rule types can
  be added by code alone, without `ALTER TYPE` migrations. `channel` remains a stable
  ENUM (email/slack/teams). `config` is a JSON blob; `schedule` is a nullable string.
- Idempotent startup migrations in `database.py` convert the former
  `notification_rule_type` ENUM to TEXT via `ALTER COLUMN ... TYPE TEXT USING ...` then
  `DROP TYPE IF EXISTS notification_rule_type`. Pattern mirrors the earlier
  `knowledge_category` TEXT migration.
- Five rule types implemented: `assignment`, `due_soon`, `overdue`, `digest`,
  `sharing_summary`.
- `/api/notifications/` router: CRUD + `POST /test/{id}` + `POST /run/{id}`.
- `NotificationLog` ORM model: written on every `test_rule` and `run_rule` call — on
  both success and failure paths — capturing `rule_type`, `channel`, `status`, `sent_at`,
  and `error` (truncated to 500 chars). Enables delivery audit.
- `_build_sharing_summary()`: resolves target meeting → deduplicates action items (by
  `task.strip().lower()`) → builds context block (transcript ≤ 6,000 chars + items +
  knowledge entries) → wraps in `<MEETING_DATA>…</MEETING_DATA>` delimiter (prompt
  injection defense) → calls `claude-haiku-4-5-20251001` (max_tokens=900) for a prose
  summary (Overview / Key Decisions / Action Items / Open Questions). Falls back to
  structured item list if API key unavailable or LLM call fails.
- `_build_run_content()` branches: `sharing_summary` → `_build_sharing_summary()`;
  all other rule types → action-item formatter with optional `project_id` filter.
- Project scoping: `config.project_id` / `config.project_name` filters all item queries
  to a specific project.
- `notification_providers.py`: SMTP, Slack Incoming Webhook, Teams Incoming Webhook
  delivery — all branding updated to "View in Synthesis".

**Notification system — frontend:**
- `SettingsPage.tsx` `AddRuleModal`: "Filter by Project" dropdown (queries
  `/api/projects/`, populates `scoped_project_id` + `scoped_project_name` in form
  state, injected into `config` on submit).
- Axios `paramsSerializer` uses `URLSearchParams.append()` for repeated-key array
  params compatible with FastAPI.

**Tracker filter:**
- `TrackerPage.tsx`: project filter always rendered (previously conditionally wrapped
  in `projects.length > 0`).

**Rebrand to Synthesis:**
- `frontend/index.html`: `<title>Synthesis</title>`
- `frontend/src/components/Layout.tsx`: sidebar brand → "Synthesis"
- `frontend/src/components/Navbar.tsx`: `/tracker` title → "Tracker"; fallback →
  "Synthesis"; ToolBtn label → "Tracker"; breadcrumb prefix → "Synthesis"
- `frontend/src/pages/LoginPage.tsx`: h1 → "Synthesis"
- `frontend/src/pages/TrackerPage.tsx`: h1 → "Tracker"
- `backend/app/main.py`: FastAPI `title` → "Synthesis API"
- `backend/app/api/chat.py`: system prompt → "You are a helpful AI assistant embedded
  in Synthesis"
- `backend/app/services/notification_providers.py`: "View in Synthesis" (4 occurrences)
- `backend/app/templates/email_assignment.html` + `email_digest.html`: all
  "Meeting Action Tracker" → "Synthesis"

**Exit criteria met:**
- Rules created in Settings UI; test and run work immediately.
- `sharing_summary` rule delivers an AI-generated prose summary (not a raw item list).
- Every delivery (sent or failed) written to `notification_logs`.
- Project filter available on notification rules and on the Tracker page.
- All user-facing surfaces display "Synthesis" as the product name.

---

## Upcoming phases

---

### Phase 5 — Security hardening

**Goal:** Close the known security gaps identified during Phase 4 audit, bringing the
system to a defensible production security posture.

**Key tasks:**
- **DB SSL:** Enable `ssl=require` + certificate verification on Supabase connection
  string (replace current `CERT_NONE`).
- **Token revocation:** Implement a Redis-backed revocation list; check on every
  authenticated request; call on logout and password change.
- **CORS:** Replace Railway wildcard with an explicit `ALLOW_ORIGINS` env var allowlist.
- **MFA rate limiting:** Lockout policy after N failed TOTP attempts; log attempts.
- **Auth event logging:** Write login, logout, failed attempts, role changes, and
  password changes to `audit_log`.
- **TOTP secret encryption:** Encrypt `totp_secret` at rest (AES-256 or KMS).
- **Security headers:** `Strict-Transport-Security`, `X-Content-Type-Options`,
  `X-Frame-Options`, `Content-Security-Policy`, `Referrer-Policy` on all responses.

**Exit criteria:** DB connection uses verified TLS; logout actually invalidates tokens;
CORS restricted to known origins; MFA brute-force blocked; security-relevant events
logged; TOTP secrets not stored in plaintext.

**Estimate:** 2–3 weeks.

---

### Phase 6 — SSO and multi-workspace tenancy

**Goal:** Enterprise-grade identity — SSO login and proper multi-workspace orgs.

**Key tasks:**
- **SSO (OIDC/SAML):** Integrate WorkOS or Auth0; provision users from org directory.
- **Multi-workspace:** Support multiple workspaces per tenant (currently one-to-one);
  cross-workspace admin views; per-workspace retention settings.
- **Owner resolution:** Match extracted `owner` strings to real user accounts for
  assignment (currently stored as `owner_label` text).
- **Notification scheduler:** Cron-based automatic rule execution (currently manual
  test/run only); per-tenant rate limits; delivery retry with backoff.

**Exit criteria:** Users can log in via SSO; two workspaces under one tenant have no
data bleed; extracted owners are matched to real users; scheduled rules execute
automatically.

**Estimate:** 4–6 weeks.

---

### Phase 7 — Recordings and meeting-platform ingestion

**Goal:** Accept actual recordings in production — closing the audio/video promise.

**Key tasks:**
- **Transcription provider:** Deepgram or AssemblyAI (managed, speaker-labelled) vs
  self-hosted Whisper (data stays in-perimeter).
- **Audio upload pipeline:** encrypted S3 storage, Celery job orchestration,
  transcription → extraction handoff, status surfaced in UI.
- **Meeting platforms:** Zoom / Teams / Google Meet recording + attendee pulls;
  calendar sync to attach recordings to the correct meeting.
- **Consent handling:** make recording/transcription explicit per jurisdiction.
- **Eval extension:** add transcription-derived (noisy) transcripts to the golden set.

**Exit criteria:** Upload a recording → tracked action items and knowledge entries;
transcription errors degrade gracefully; golden-set examples still meet thresholds.

**Estimate:** 4–6 weeks.

---

### Phase 8 — Workflow integrations and reporting

**Goal:** Fit into where teams already work; make the data legible to leadership.

**Key tasks:**
- **PM-tool adapters:** one-way push (then optionally two-way sync) of action items to
  Jira / Asana / ClickUp / Linear behind a common adapter interface.
- **Dedup at scale:** match recurring-meeting items so weekly standing tasks are not
  recreated each cycle.
- **Enriched dashboards:** completion rate, throughput over time, overdue tracking,
  per-meeting and per-owner rollups; CSV/PDF export.
- **Knowledge analytics:** category distribution, most-cited decisions, open-question
  age, risk heat maps.
- **BI feed:** aggregated, non-sensitive metrics export for Datorama / Tableau.

**Exit criteria:** Items appear in the chosen PM tool; leaders can see
completion/throughput; no sensitive content leaks via exports.

**Estimate:** 4–6 weeks.

---

### Phase 9 — Hardening, compliance, and scale

**Goal:** Production-grade reliability, governance, and the compliance posture clients
will ask for.

**Key tasks:**
- **Observability:** distributed tracing/metrics; extraction telemetry dashboards;
  cost dashboards per tenant/model; alerting on errors, backlog, and spend.
- **Cost controls:** per-tenant rate limits and LLM spend caps; Batch API for bulk
  historical reprocessing.
- **Reliability:** retry/backoff on provider failures; job backlog handling; load/scale
  testing on long transcripts and many concurrent tenants.
- **Compliance:** data-retention/deletion (including backups), audit-log completeness,
  DPAs with processors, SOC 2 / GDPR / HIPAA-if-applicable path.
- **Accessibility:** keyboard navigation, screen reader support, reduced-motion
  preferences throughout the UI.
- **Final security review and pen test.**

**Exit criteria:** System stays within cost and latency budgets under load; recovers
from provider outages; honors deletion across all storage; passes security and
accessibility reviews.

**Estimate:** 4–6 weeks (compliance certification runs longer in parallel).

---

## Milestone summary

| Phase | Status | Outcome |
|---|---|---|
| 0 — Foundations | ✓ Done | Railway + Supabase stack running end-to-end |
| 1 — MVP core | ✓ Done | Auth, extraction, tracker, dashboard |
| 2 — Projects + chat + admin | ✓ Done | Projects, AI assistant, admin panel |
| 3 — Knowledge base | ✓ Done | Auto-classified knowledge, browsable + editable |
| 4 — Notifications + rebrand | ✓ Done | Email/Slack/Teams alerts, AI summary, Synthesis brand |
| 5 — Security hardening | Next | Close known gaps; defensible production posture |
| 6 — SSO + multi-workspace | Planned | Enterprise identity, notification scheduler |
| 7 — Recordings + platforms | Planned | Audio pipeline, Zoom/Teams/Meet pulls |
| 8 — Integrations + reporting | Planned | PM sync, dashboards, BI feed |
| 9 — Hardening + compliance | Planned | SOC 2 / GDPR path, scale, accessibility |

---

## Cross-cutting practices (every phase)

- Run the eval suite on every prompt/model/parser change; recall regressions block merge.
- Keep the security guardrails in `CLAUDE.md` true at all times, not just in Phase 5.
- Never use real meeting data outside production; debug with seeded test tenants.
- Add each real-world extraction miss to the golden set before fixing it.
- Dual-register collection routes: `@router.get("")` (no schema) + `@router.get("/")`
  to work correctly with the ASGI trailing-slash middleware.

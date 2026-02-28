# Product Requirements Document (PRD)

## 0. Document Metadata
- Product: System V2 (LLM-first Knowledge Intelligence Platform)
- Status: Draft v2.1 (translated and expanded from `definicja_produktu.md` + aligned with `architektura_techniczna.md` and `ui.md`)
- Language: English
- Intended release: v1

## 1. Product Vision
System V2 is an LLM-first platform for asking questions across ingested knowledge sources (PDF and web content), where the model decides the best response format: text, source images, or diagrams.

The product must behave like a natural chat assistant, not a manual retrieval tool. Retrieval is support logic; reasoning and response strategy are model-led, and the model may use relevant general knowledge beyond ingested sources when it improves the answer.

## 2. Problem Statement
Teams need one interface to:
- Understand document content beyond keyword matching.
- Synthesize information across multiple sources.
- Retrieve visual evidence (figures/screenshots) when relevant.
- Maintain conversational context without manual toggles.
- Get practical, expert-style answers instead of raw fact snippets.

Existing internal search tools are typically chunk-centric and require technical controls. This product removes those controls from end users.

## 3. Goals and Non-Goals
### 3.1 Goals (v1)
- Provide ChatGPT-like conversational UX for enterprise documents.
- Support semantic retrieval for both text and image meaning.
- Enable multi-document synthesis in one answer.
- Support diagram generation (Graphviz default).
- Keep user UX minimal (no `top_k`, response-format, or context toggles).
- Keep all product UI text in English.
- Deliver the application in Dockerized form for consistent local and server deployment.
- Produce answers in natural language synthesis, not extractive copy-paste retrieval.

### 3.2 Non-Goals (v1)
- Public document catalog for regular users.
- User-facing retrieval tuning controls.
- Complex manual query builder.

## 4. User Roles
- User: can sign in with Google SSO and use `/app` chat.
- Admin: all User permissions plus Admin Panel access.
- Super-admin (fallback local login): emergency admin access, including admin role management.

## 5. Product Scope
### 5.1 End-User App (`/app`)
- Single-column chat interface.
- Composer: multiline input (`Enter` newline, `Ctrl/Cmd+Enter` send).
- `Send` disabled during in-flight requests; local loading state only.
- Optional `New chat` action resets `conversation_id`.
- Assistant answers can include:
  - text,
  - optional webpage references when useful,
  - inline thumbnails from relevant document images,
  - diagram image output (if applicable).

### 5.2 Admin Panel (`/admin`)
- Documents: list, filter, view metadata, download, delete with confirmation.
- Ingest: multi-file PDF upload queue with per-file status.
- Re-ingest: admins can re-run ingestion for an existing document in v1.
- Ask History & Audit: admin-only visibility into Q/A events and retrieval evidence metadata.
- Users & Roles: assign/remove `admin` role by email.
- Settings: visibility into domain allow-list, public download policy, model and image-policy configuration.

### 5.3 Ingestion
- PDF and Web ingestion supported.
- For PDF:
  1. Store source file.
  2. Extract text -> chunk -> embed.
  3. Extract images -> store assets.
  4. Run mandatory vision captioning for eligible images.
  5. Embed captions.
  6. Mark document status `ready`.
- For Web:
  - Public fetch and Google OAuth delegation fetch only (v1 scope).
  - HTML snapshot + text/image processing path equivalent to PDF.

## 6. Functional Requirements
### FR-001 Authentication and Domain Access
- Login method must be Google SSO.
- Backend must validate user email domain against `ALLOWED_GOOGLE_DOMAINS`.
- `/ask` must require an authenticated Google-backed user session.
- Allowed config forms:
  - `*` (all domains),
  - single domain (example: `netaxis.be`),
  - comma-separated list.
- Default allow-list is `netaxis.be`.

Acceptance criteria:
- Unauthorized domain users see `Access denied`.
- Allowed domain users are redirected to `/app`.
- Unauthenticated `/ask` requests are rejected (`401` or equivalent auth redirect policy).

### FR-002 Authorization and Admin Gate
- Admin Panel access must require `admin` role.
- Non-admin access to `/admin` must show fallback super-admin login.
- Super-admin credentials are local (`username` + `password`) and checked server-side.
- Super-admin password is stored as bcrypt hash in environment.

Acceptance criteria:
- Role checks are enforced on backend routes.
- Super-admin login failures return clear error and are rate-limited.
- Admin/security actions are audit logged.

### FR-003 Conversational UX Rules
- Chat experience must not expose manual controls for:
  - `top_k`,
  - response format selection,
  - conversation-context toggle.
- Conversation context is retained server-side; client stores `conversation_id`.

Acceptance criteria:
- UI contains only natural language input + send/new chat actions.
- Follow-up questions can rely on prior chat turns in same conversation.

### FR-004 Image Ingestion Policy (Mandatory Vision)
- Vision processing is mandatory at startup; app must fail to start if vision provider is unavailable.
- For scanned/image-heavy sources in v1, semantic understanding is handled by the same vision-caption pipeline (LLM visual inspection -> caption -> embedding), with no separate OCR engine.
- Image is eligible for captioning only if all are true:
  - `width >= 320`,
  - `height >= 320`,
  - `width * height >= 200000`,
  - `file_bytes >= 15000`,
  - `aspect_ratio <= 8`.
- Per page, process maximum 5 images by largest area.
- Caption max length: 1200 characters.
- Caption embedding model: `text-embedding-3-large`.

Acceptance criteria:
- Eligible images have stored asset metadata and caption embeddings.
- Ineligible images are skipped with reason logged.

### FR-005 Retrieval and Answer Generation (`/ask`)
- Pipeline:
  1. Embed user question.
  2. Dense retrieval from `text_chunks` and `image_captions`.
  3. Build context pack.
  4. Generate answer using answer provider.
- LLM decides whether answer includes:
  - text only,
  - source images,
  - generated diagram,
  - confidence score.
- Confidence must be returned and displayed to the end user as a percentage (`0-100%`).
- Answers are not limited to retrieved document text; the model may add relevant domain knowledge if consistent with user intent.
- Answers should be written in the model's own words (synthesized response style).

Acceptance criteria:
- No user-facing retrieval control is required for valid operation.
- End-user responses do not require formal citation blocks.

### FR-006 References and Evidence Presentation
- The app does not require formal PDF citation rendering in end-user chat responses.
- If references are shown in the response, they must be webpage links and only when useful to the user.
- PDF page/chunk citations are not shown in end-user responses.
- Inline image previews from ingested documents remain supported when they help explain the answer.

Acceptance criteria:
- Clicking thumbnail opens larger preview/modal.
- No `Sources (N)` panel is required for valid end-user operation.

### FR-007 Diagram Intelligence
- System may generate:
  - architecture diagram,
  - process flow,
  - relationship schema.
- Diagram capability is always enabled; model decides when to generate based on user intent and context.
- Default generator: Graphviz.
- UI displays rendered PNG inline; optional expandable DOT view.
- Optional alternative generators: OpenAI/Ollama image generation when enabled.

Acceptance criteria:
- Diagram requests produce either a valid diagram or explicit fallback explanation.

### FR-008 Multi-Document Synthesis
- Model must synthesize across multiple retrieved sources when needed.
- Response should identify key source documents when conclusions rely on multiple references.

Acceptance criteria:
- Cross-document questions return a unified answer without requiring formal source citation formatting.

### FR-009 Data Model (Minimum Tables)
- `documents`, `text_chunks`, `document_images`, `image_captions`, `users`, `ask_history`.
- `users.role` values: `user | admin | super_admin`.
- `ask_history` stores:
  - asking user identity (`user_id`, user email),
  - question and generated answer,
  - timestamp and conversation context identifiers,
  - confidence and fallback indicators,
  - retrieval evidence metadata (chunk IDs, document IDs, PDF filenames/source names, webpage URLs, related image IDs).

Acceptance criteria:
- Delete-document action uses a confirmation modal and then performs immediate hard delete of the document and derived assets.

### FR-010 Public Asset Controls
- Public asset access (thumbnails, diagrams, PDF download) is controlled by environment flags (example: `PUBLIC_DOCUMENT_DOWNLOADS`).

Acceptance criteria:
- Behavior changes immediately according to flag values (or after configured cache refresh).

### FR-011 Containerization and Runtime Packaging
- The full application must be Dockerized for v1.
- Local startup must be supported via `docker compose`.
- A single top-level `docker-compose.yml` must define the runnable v1 stack.
- Mandatory services in the v1 Compose stack:
  - `frontend` (Next.js app),
  - `backend` (FastAPI API),
  - `worker` (asynchronous ingest/re-ingest jobs),
  - `postgres` with `pgvector` (metadata + vector retrieval),
  - `redis` (queue and short-lived operational state),
  - `minio` (object storage for source files, extracted images, and generated assets).
- Container build definitions must be committed and versioned with the codebase.
- Service startup order and health checks must be defined for required services.
- Runtime configuration must be injected through environment variables (not hardcoded).

Acceptance criteria:
- A clean environment can run the platform using `docker compose up -d --build`.
- All required services reach healthy state and support end-to-end login, ingest, and `/ask` flow.
- Reproducible rebuild is possible without manual host-level dependency setup.

### FR-012 Re-ingest Capability (Admin)
- Admin UI must provide a `Re-ingest` action for existing documents.
- Re-ingest must rebuild derived artifacts (chunks, image metadata, captions, embeddings) from the current source.
- Re-ingest status and failures must be visible in admin flows.

Acceptance criteria:
- Admin can trigger re-ingest from document context without manual DB operations.
- Re-ingest completion updates document metadata and retrieval artifacts used by `/ask`.

### FR-013 Ask History Visibility and Access Control
- Every `/ask` interaction must be persisted in `ask_history` with user and evidence metadata.
- Ask history must be visible only to `admin` and `super_admin` in the Admin Panel.
- Non-admin users must not access global ask history data or other users’ ask records.

Acceptance criteria:
- Admin users can filter and inspect ask-history records by user, date, and source type.
- Non-admin access attempts to ask-history endpoints are rejected (`403` or equivalent).

### FR-014 No-Retrieval Fallback Policy
- If initial retrieval returns no usable chunks/captions, backend must run one fallback retrieval pass:
  - internally rewrite/expand the user question,
  - rerun retrieval with broader matching constraints.
- If fallback retrieval still returns no usable context:
  - for in-scope/domain-relevant questions: provide a best-effort synthesized answer using model knowledge, explicitly marked as not grounded in indexed sources, with lower confidence.
  - for clearly out-of-scope questions: return a short scope-limited refusal.
- End-user response must never silently pretend source grounding when none was retrieved.

Acceptance criteria:
- Response explicitly states when indexed sources were not found.
- Domain-relevant no-retrieval responses remain helpful and non-empty.
- Clearly off-scope questions return a concise scope-boundary message.
- Ask history records retrieval outcome and fallback path used.

### FR-015 README and Configuration Documentation Governance
- `README.md` is a required living operational document for the application.
- `README.md` must include:
  - deployment instructions (including Docker Compose startup),
  - configuration instructions,
  - detailed environment variable reference.
- Environment variable reference must define, for each variable:
  - name,
  - purpose,
  - required vs optional,
  - default value (if any),
  - example value,
  - security sensitivity notes (if applicable).
- Any code change that adds or modifies configuration, deployment behavior, or runtime dependencies must include corresponding `README.md` updates in the same change set.
- After each coding and testing cycle, `README.md` must be reviewed and updated if needed to match the current state of the app.

Acceptance criteria:
- No new or changed environment variable is merged without `README.md` documentation.
- Deployment and configuration steps in `README.md` are executable and consistent with the current codebase.
- Pull request checklist includes explicit confirmation that `README.md` was reviewed/updated.

## 7. Provider and Model Requirements
- Architecture must be provider-agnostic for:
  - answering,
  - embeddings,
  - vision.
- Supported provider families: OpenAI and Ollama.
- Baseline configured models:
  - Answering: `gpt-5.2`
  - Vision: `gpt-5.2`
  - Embeddings: `text-embedding-3-large`

## 8. Non-Functional Requirements
- Security:
  - enforce RBAC on backend routes,
  - store fallback admin secrets as hashes only,
  - apply rate limiting on fallback login.
- Reliability:
  - ingestion failures should be file-scoped, not batch-fatal.
- Performance:
  - `/ask` latency SLO target: p50 <= 10s and p95 <= 25s.
- Observability:
  - log ingestion outcomes, skipped image reasons, auth failures, and `/ask` metadata.
- Deployment:
  - v1 delivery requires Docker images and Compose-based runnable stack.

## 9. Success Criteria (v1)
The system is successful if:
- typo-tolerant questions still produce useful answers,
- image semantics are searchable and used in answers,
- multi-document synthesis works for realistic business questions,
- diagram generation works when requested,
- end-user UX feels as simple as ChatGPT.

## 10. Out of Scope / vNext
- End-user conversation history list in `/app` (deferred to v1.1+).
- Workspace-level multi-tenancy controls.
- Rich human feedback loop (answer rating/retraining).
- Public browsing of full document inventory for non-admin users.

## 11. Open Questions Requiring Product Decisions
No open product questions at this stage.

## 12. Confirmed Decisions (From Product Review)
- `/ask` is available to authenticated Google users only.
- Confidence is visible to end users as a percentage.
- Image semantic indexing is mandatory and follows the existing image eligibility policy.
- Document deletion is immediate (hard delete) after explicit confirmation.
- Diagram generation is always enabled; the model decides when to produce it.
- Web ingestion scope in v1: public pages and Google delegated pages only.
- Product UI language is English only.
- `/ask` performance target: p50 <= 10s and p95 <= 25s.
- Scanned/image-heavy input in v1 uses vision-caption semantic processing; no separate OCR engine.
- Admin re-ingest capability is included in v1.
- End-user conversation history list is deferred to v1.1+.
- No additional compliance constraints are defined for v1 at this stage.
- v1 must ship as a Dockerized application.
- v1 must use one `docker-compose.yml`.
- v1 Docker mandatory services: `frontend`, `backend`, `worker`, `postgres+pgvector`, `redis`, `minio`.
- Answers should be synthesized in the model’s own words and may use relevant general knowledge beyond ingested sources.
- Formal citation blocks are not required in end-user answers.
- If references are included in answers, include webpage links only when useful.
- `README.md` must be maintained as the source of truth for deployment and configuration, including environment variable documentation.

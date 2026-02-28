# ContextForge

ContextForge is an LLM-first knowledge assistant for company documents and approved webpages. It behaves like natural chat, supports multimodal retrieval (text + images), and can generate diagrams when useful.

## Current State
This repository now includes an MVP scaffold:
- `frontend` (Next.js app)
- `backend` (FastAPI API with DB bootstrap, `/api/v1/ask`, admin APIs, and PDF ingestion endpoint)
- `worker` (background process skeleton)
- `docker-compose.yml` (single stack file)
- Product docs and PRD (`prd_v2_en.md`)

## Repository Layout
- `frontend/`: user-facing web app
- `backend/`: API service and application config
- `worker/`: async job runner skeleton
- `docker-compose.yml`: local deployment stack
- `.env.example`: baseline runtime configuration
- `.github/`: issue/PR templates with README and ENV documentation checks

## Deployment (Docker Compose)
1. Create local environment file:
```bash
cp .env.example .env
```
If local ports are already in use, change `FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `MINIO_PORT`, and `MINIO_CONSOLE_PORT` in `.env`.
2. Start the full stack:
```bash
docker compose up -d --build
```
3. Stop and remove containers/volumes:
```bash
docker compose down -v
```

## Google OAuth Setup (Step-by-Step)
Before first login, create Google OAuth credentials and place them in `.env`.

1. Create (or select) a Google Cloud project.
2. Open Google Cloud Console and go to Google Auth Platform.
3. Configure consent screen/branding:
   - Set app name.
   - Set support email.
   - Set developer contact email.
   - Choose audience (`External` is typical for local dev).
   - If asked, add your Google account as a test user.
4. Go to `APIs & Services` -> `Credentials`.
5. Click `Create credentials` -> `OAuth client ID`.
6. Choose application type: `Web application`.
7. Add OAuth URLs using your frontend port:
   - Authorized JavaScript origin: `http://localhost:${FRONTEND_PORT}`
   - Authorized redirect URI: `http://localhost:${FRONTEND_PORT}/api/auth/callback/google`
8. Create the client and copy values into `.env`:
   - `GOOGLE_CLIENT_ID=...`
   - `GOOGLE_CLIENT_SECRET=...`
9. Ensure `NEXTAUTH_URL` matches your frontend URL, for example:
   - `NEXTAUTH_URL=http://localhost:${FRONTEND_PORT}`
10. Restart services:
```bash
docker compose up -d --build
```

If your Google Workspace policy blocks external OAuth apps, you may need a Workspace admin to allow or publish the app.

## Service Endpoints (Local)
- Frontend: `http://localhost:${FRONTEND_PORT}` (default `http://localhost:3000`)
- Backend health: `http://localhost:${BACKEND_PORT}/health` (default `http://localhost:8000/health`)
- Backend API health: `http://localhost:${BACKEND_PORT}/api/v1/health`
- Frontend ask proxy endpoint: `http://localhost:${FRONTEND_PORT}/api/ask`
- Frontend admin document endpoints: `http://localhost:${FRONTEND_PORT}/api/admin/documents`
- Frontend admin webpage ingest endpoint: `http://localhost:${FRONTEND_PORT}/api/admin/webpages`
- Frontend admin linked-page ingest endpoint: `http://localhost:${FRONTEND_PORT}/api/admin/webpages/linked`
- Frontend admin docs-set endpoint: `http://localhost:${FRONTEND_PORT}/api/admin/docs-sets`
- Frontend admin discovered-links endpoint: `http://localhost:${FRONTEND_PORT}/api/admin/discovered-links`
- Frontend admin ask-history endpoint: `http://localhost:${FRONTEND_PORT}/api/admin/ask-history`
- Backend PDF ingest endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/ingest/pdf`
- Backend webpage ingest endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/ingest/webpage`
- Backend linked-page ingest endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/ingest/webpage/linked`
- Backend docs-set list endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/docs-sets`
- Backend discovered-links list endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/discovered-links?source_document_id={id}`
- Backend admin documents list endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/documents`
- Backend admin document delete endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/documents/{document_id}`
- Backend admin ask-history list endpoint: `http://localhost:${BACKEND_PORT}/api/v1/admin/ask-history`
- MinIO API: `http://localhost:9000`
- MinIO Console: `http://localhost:9001`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

## Build and Validation Commands
- Validate compose file:
```bash
docker compose config
```
- Check running services:
```bash
docker compose ps
```
- Tail backend logs:
```bash
docker compose logs -f backend
```
- Test PDF ingestion (example):
```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/pdf" \
  -H "Expect:" \
  -H "x-user-email: your-email@netaxis.be" \
  -H "x-user-name: Your Name" \
  -F "file=@Fusion4Broadworks_Product_Description.pdf"
```
- Test webpage ingestion (example):
```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/webpage" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@netaxis.be" \
  -H "x-user-name: Your Name" \
  -d '{"url":"https://example.com/docs/start","docs_set_name":"Example API Docs"}'
```
- Add another page to the same docs set (example uses existing `docs_set_id=1`):
```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/webpage" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@netaxis.be" \
  -H "x-user-name: Your Name" \
  -d '{"url":"https://example.com/docs/auth","docs_set_id":1}'
```
- Test linked-page batch ingest from discovered links (same-domain only):
```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/webpage/linked" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@netaxis.be" \
  -H "x-user-name: Your Name" \
  -d '{"source_document_id": 12, "max_pages": 20}'
```

## Configuration
All runtime configuration must come from environment variables (never hardcode secrets).

### Environment Variable Reference
| Variable | Required | Default | Example | Purpose | Sensitive |
|---|---|---|---|---|---|
| `APP_ENV` | No | `development` | `production` | Runtime mode | No |
| `FRONTEND_PORT` | No | `3000` | `13000` | Host port mapped to frontend container port 3000 | No |
| `BACKEND_PORT` | No | `8000` | `18000` | Host port mapped to backend container port 8000 | No |
| `POSTGRES_PORT` | No | `5432` | `15432` | Host port mapped to Postgres | No |
| `REDIS_PORT` | No | `6379` | `16379` | Host port mapped to Redis | No |
| `MINIO_PORT` | No | `9000` | `19000` | Host port mapped to MinIO API | No |
| `MINIO_CONSOLE_PORT` | No | `9001` | `19001` | Host port mapped to MinIO console | No |
| `APP_URL` | No | `http://localhost:3000` | `https://app.company.com` | Public frontend URL | No |
| `API_URL` | No | `http://localhost:8000` | `https://api.company.com` | Public API URL | No |
| `BACKEND_INTERNAL_URL` | No | `http://backend:8000` | `http://backend:8000` | Backend URL used by frontend server routes | No |
| `ALLOWED_GOOGLE_DOMAINS` | Yes | `netaxis.be` | `netaxis.be,google.com` | Allowed Google email domains (`*` for all) | No |
| `ADMIN_EMAILS` | Yes | - | `alice@netaxis.be,bob@netaxis.be` | Comma-separated admin users allowed to access admin APIs/UI (`*` for all allowed-domain users) | No |
| `GOOGLE_CLIENT_ID` | Yes | - | `123...apps.googleusercontent.com` | Google SSO OAuth client id | No |
| `GOOGLE_CLIENT_SECRET` | Yes | - | `<secret>` | Google SSO OAuth client secret | Yes |
| `NEXTAUTH_URL` | Yes | `http://localhost:3000` | `https://app.company.com` | Auth callback base URL | No |
| `NEXTAUTH_SECRET` | Yes | - | `<secret>` | Session/cookie signing secret | Yes |
| `SUPERADMIN_USERNAME` | Yes | `superadmin` | `superadmin` | Emergency admin login username | No |
| `SUPERADMIN_PASSWORD_HASH` | Yes | - | `<bcrypt_hash>` | Emergency admin password hash (bcrypt) | Yes |
| `PUBLIC_DOCUMENT_DOWNLOADS` | No | `false` | `true` | Enables end-user PDF download links | No |
| `ANSWER_PROVIDER` | Yes | `openai` | `ollama` | Answering provider (`openai|ollama`) | No |
| `VISION_PROVIDER` | Yes | `openai` | `ollama` | Vision provider (`openai|ollama`) | No |
| `EMBEDDINGS_PROVIDER` | Yes | `openai` | `ollama` | Embeddings provider (`openai|ollama`) | No |
| `ANSWER_MODEL` | No | `gpt-5.2` | `gpt-5.2` | Answering model id | No |
| `VISION_MODEL` | No | `gpt-5.2` | `gpt-5.2` | Vision model id | No |
| `EMBEDDINGS_MODEL` | No | `text-embedding-3-large` | `text-embedding-3-large` | Embedding model id | No |
| `ASK_TOP_K` | No | `6` | `8` | Number of top retrieved chunks used for answering | No |
| `OPENAI_API_KEY` | Conditionally | - | `<secret>` | Required when a provider is `openai` | Yes |
| `OPENAI_TIMEOUT_SECONDS` | No | `60` | `60` | Timeout for OpenAI Responses API requests | No |
| `OLLAMA_BASE_URL` | Conditionally | `http://ollama:11434` | `http://host.docker.internal:11434` | Required when a provider is `ollama` | No |
| `WEB_FETCH_TIMEOUT_SECONDS` | No | `20` | `20` | Timeout for webpage fetch requests during ingest | No |
| `WEB_INGEST_MAX_CHARS` | No | `120000` | `100000` | Maximum normalized webpage text characters to keep before chunking | No |
| `WEB_INGEST_MAX_CHUNKS` | No | `120` | `150` | Maximum number of chunks embedded per webpage ingest | No |
| `WEB_INGEST_MAX_IMAGES` | No | `60` | `80` | Maximum discovered webpage images to download/process per page | No |
| `WEB_INGEST_USER_AGENT` | No | `ContextForgeBot/1.0` | `ContextForgeBot/1.0 (+https://app.company.com)` | User-Agent used when fetching webpages | No |
| `GOOGLE_DELEGATED_BEARER_TOKEN` | No | - | `<oauth_access_token>` | Optional bearer token for Google delegated page fetches (docs/drive/sites) | Yes |
| `POSTGRES_DB` | Yes | `contextforge` | `contextforge` | Postgres database name | No |
| `POSTGRES_USER` | Yes | `contextforge` | `contextforge` | Postgres user | No |
| `POSTGRES_PASSWORD` | Yes | `contextforge` | `<secret>` | Postgres password | Yes |
| `DATABASE_URL` | Yes | `postgresql://contextforge:contextforge@postgres:5432/contextforge` | `postgresql://user:pass@postgres:5432/db` | Main metadata/vector DB connection | Yes |
| `REDIS_URL` | Yes | `redis://redis:6379/0` | `redis://redis:6379/0` | Queue/cache/rate-limit backend | No |
| `S3_ENDPOINT` | Yes | `http://minio:9000` | `http://minio:9000` | Object storage endpoint | No |
| `S3_ACCESS_KEY` | Yes | `minioadmin` | `minioadmin` | Object storage access key | Yes |
| `S3_SECRET_KEY` | Yes | `minioadmin` | `<secret>` | Object storage secret key | Yes |
| `S3_BUCKET_DOCUMENTS` | Yes | `documents` | `documents` | Bucket for source files | No |
| `S3_BUCKET_ASSETS` | Yes | `assets` | `assets` | Bucket for extracted/generated assets | No |
| `IMAGE_MIN_WIDTH` | No | `320` | `320` | Vision eligibility minimum width | No |
| `IMAGE_MIN_HEIGHT` | No | `320` | `320` | Vision eligibility minimum height | No |
| `IMAGE_MIN_AREA` | No | `200000` | `200000` | Vision eligibility minimum area | No |
| `IMAGE_MIN_BYTES` | No | `15000` | `15000` | Vision eligibility minimum file size | No |
| `IMAGE_MAX_ASPECT_RATIO` | No | `8` | `8` | Vision eligibility maximum aspect ratio | No |
| `IMAGE_MAX_PER_PAGE` | No | `5` | `5` | Max images captioned per page | No |
| `CAPTION_MAX_CHARS` | No | `1200` | `1200` | Vision caption max length | No |
| `INGEST_CHUNK_SIZE_CHARS` | No | `1200` | `1500` | Character length for text chunking during PDF ingest | No |
| `INGEST_CHUNK_OVERLAP_CHARS` | No | `180` | `200` | Chunk overlap size during PDF ingest | No |
| `INGEST_MAX_CHUNKS` | No | `200` | `300` | Safety cap on number of chunks embedded per ingest request | No |
| `INGEST_MAX_VISION_IMAGES` | No | `0` | `40` | Global cap on how many eligible images are vision-captioned per ingest (`0` = no cap) | No |
| `ASK_LATENCY_P50_TARGET_MS` | No | `10000` | `10000` | p50 latency target | No |
| `ASK_LATENCY_P95_TARGET_MS` | No | `25000` | `25000` | p95 latency target | No |
| `WORKER_POLL_SECONDS` | No | `5` | `10` | Worker heartbeat/poll interval | No |

Notes:
- "Conditionally" means required only when selected provider needs it.
- `ADMIN_EMAILS` must be configured for admin ingestion, document deletion, and ask-history visibility.
- In Docker Compose runs, keep `BACKEND_INTERNAL_URL=http://backend:8000` (container-to-container address), even when host `BACKEND_PORT` is mapped to another port like `18000`.
- Webpage ingestion accepts only publicly reachable URLs and blocks private/local network addresses.
- Linked-page batch ingest is intentionally constrained to discovered same-domain links from one source page per run.
- Never commit real secrets. Use placeholders in docs and examples.

## Documentation Governance
- `README.md` is a living operational document.
- Any change to configuration, deployment, runtime behavior, or dependencies must update this file in the same PR.
- Every coding/testing cycle should include a README consistency review.

## Implemented in This Milestone
- Google sign-in via NextAuth in frontend (`/api/auth/*`).
- Domain allow-list checks in frontend sign-in callback and backend `/api/v1/ask`.
- Backend startup schema bootstrap (`users`, `documents`, `text_chunks`, `document_images`, `image_captions`, `ask_history`).
- First `/api/v1/ask` vertical slice:
  - requires authenticated user identity via frontend proxy,
  - performs multimodal retrieval (text chunks + image captions) with broadened retry (keyword fallback when needed),
  - applies no-retrieval fallback policy,
  - persists ask history and evidence metadata,
  - returns optional relevant image evidence links.
- PDF ingestion vertical slice (`/api/v1/admin/ingest/pdf`):
  - stores PDF in object storage,
  - extracts text and chunks it,
  - extracts page images, filters by vision policy, captions eligible images with the configured vision provider, and embeds captions,
  - creates OpenAI embeddings for text chunks and image captions,
  - stores vectorized chunks in Postgres/pgvector and marks document ready.
- Admin panel v1:
  - admin-only frontend UI for PDF upload, indexed document monitoring, and ask-history review,
  - admin-only backend APIs for listing/deleting documents and listing ask-history traces,
  - document deletion includes confirmation in UI and immediate removal of DB rows plus storage assets.
- Webpage ingestion v1:
  - admin-only ingest endpoint and UI action for URL ingestion with `docs_set` grouping metadata,
  - stores HTML snapshots for traceability and re-ingest support (`documents.source_storage_key`),
  - extracts narrative text plus structured table chunks (`table_summary` and `table_row` with numeric-aware metadata),
  - discovers webpage images, downloads eligible public assets, captions images with vision model, and embeds captions,
  - runs unified retrieval across text chunks, table chunks, and image captions,
  - discovers page links and stores them in admin-visible discovered-links queue,
  - supports one-by-one link ingest and controlled same-domain batch ingest (`max_pages` bounded),
  - supports public pages by default; Google delegated fetch can use optional bearer token configuration.

## Provider Behavior (Current)
- `ANSWER_PROVIDER=openai`: implemented. Backend calls OpenAI Responses API using `ANSWER_MODEL` and `OPENAI_API_KEY`.
- `ANSWER_PROVIDER=ollama`: placeholder only. Request succeeds with a clear "not implemented yet" message; real Ollama generation is still pending.
- `VISION_PROVIDER=openai`: implemented for PDF and webpage image caption generation during ingest.
- `VISION_PROVIDER=ollama`: placeholder only (not implemented yet).
- `EMBEDDINGS_PROVIDER=openai`: implemented for ingestion and retrieval.
- `EMBEDDINGS_PROVIDER=ollama`: not implemented yet for ingestion/retrieval.

## Git Workflow
- Use short-lived feature branches from `main`.
- Use Conventional Commits (example: `feat(api): add no-retrieval fallback mode`).
- Keep PRs focused and ensure README + ENV documentation checks are completed.

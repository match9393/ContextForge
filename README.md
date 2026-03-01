# ContextForge

ContextForge is an LLM-first internal knowledge assistant for company documents and approved webpages.
It includes:
- a chat assistant UI,
- an admin UI for ingestion and governance,
- a FastAPI backend with retrieval + answer generation,
- PostgreSQL/pgvector, Redis, and MinIO infrastructure,
- a worker skeleton process.

## Current Scope

Implemented now:
- Google sign-in with domain allow-list.
- Chat-style assistant UI with session chat history in sidebar.
- Rename chat by clicking active title.
- Delete chat from sidebar with `x`.
- Conversation-scoped asks (`conversation_id`) forwarded to backend and stored in `ask_history`.
- PDF ingestion pipeline (text chunks + image extraction + optional image caption embeddings).
- Webpage ingestion pipeline (text, table-aware chunks, discovered links, optional image captions).
- Admin tabs: `Ingest`, `Documents`, `Documentation Sets`, `Ask History`, `Users & Roles`.
- `Documents` shown before `Documentation Sets`.
- Discovered links shown in modal (not inline on page).
- Multi-select discovered links ingest plus single-link ingest and bounded same-domain batch ingest.
- Ask history tab with user filter.
- Super-admin login flow for users not on `ADMIN_EMAILS`.
- Generated answer visuals through OpenAI Images API using `GENERATED_IMAGE_MODEL` (default `gpt-image-1.5`).
- Image generation guarded to explicit visual intent and stronger anti-cropping prompt rules.

Not fully implemented:
- `ANSWER_PROVIDER=ollama` (placeholder message only).
- `VISION_PROVIDER=ollama` (not implemented for ingest).
- `EMBEDDINGS_PROVIDER=ollama` (not implemented for ingest/retrieval).
- `PUBLIC_DOCUMENT_DOWNLOADS` currently reserved (no active code path yet).
- `APP_URL`, `API_URL`, `ASK_LATENCY_P50_TARGET_MS`, `ASK_LATENCY_P95_TARGET_MS` are currently informational/reserved.

## Repository Layout

- `frontend/`: Next.js app (`/` assistant, `/admin` admin panel, server API routes).
- `backend/`: FastAPI app (`/api/v1/ask`, admin ingest/management endpoints, DB bootstrap).
- `worker/`: background worker skeleton with heartbeat loop.
- `docker-compose.yml`: local full-stack orchestration.
- `.env.example`: complete runtime configuration template.
- `definicja_produktu.md`, `architektura_techniczna.md`, `ui.md`, `prd_v2_en.md`: product/architecture docs.

## Prerequisites

- Docker Engine + Docker Compose plugin.
- Google OAuth credentials (Web app) for login.
- OpenAI API key (for current implemented provider path).

Optional if running without Docker:
- Node.js 20.x.
- Python 3.11.
- PostgreSQL with `pgvector`.
- Redis.
- S3-compatible object storage (MinIO recommended).

## Quick Start (Docker Compose)

1. Copy environment template:

```bash
cp .env.example .env
```

2. Fill minimum required values in `.env`:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `NEXTAUTH_SECRET`
- `NEXTAUTH_URL`
- `OPENAI_API_KEY` (when providers are `openai`)
- `ALLOWED_GOOGLE_DOMAINS` (set to your org domain, or `*` only for controlled testing)

3. Optional but recommended:
- set `ADMIN_EMAILS` for direct admin users (comma-separated emails).
- set `SUPERADMIN_PASSWORD_HASH` to enable super-admin login.
- replace `SUPERADMIN_SESSION_SECRET`.
- change host ports (`FRONTEND_PORT`, `BACKEND_PORT`, etc.) if defaults are occupied.

4. Build and start:

```bash
docker compose up -d --build
```

5. Verify:

```bash
docker compose ps
curl http://localhost:${BACKEND_PORT:-8000}/health
```

6. Open:
- Assistant: `http://localhost:${FRONTEND_PORT:-3000}`
- Admin UI: `http://localhost:${FRONTEND_PORT:-3000}/admin`
- MinIO console: `http://localhost:${MINIO_CONSOLE_PORT:-9001}`

Stop stack:

```bash
docker compose down
```

Stop and remove named volumes:

```bash
docker compose down -v
```

## Google OAuth Setup

1. Create/select a Google Cloud project.
2. Configure OAuth consent screen.
3. Create OAuth client credentials (`Web application`).
4. Add redirect settings:
- Authorized origin: `http://localhost:<FRONTEND_PORT>`
- Redirect URI: `http://localhost:<FRONTEND_PORT>/api/auth/callback/google`
5. Put credentials in `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
6. Ensure `NEXTAUTH_URL` matches frontend URL.
7. Rebuild/restart:

```bash
docker compose up -d --build
```

## Admin Access Model

- Step 1: user must pass Google domain allow-list (`ALLOWED_GOOGLE_DOMAINS`).
- Step 2: admin page access is granted if:
- user email is in `ADMIN_EMAILS`, or
- user has active super-admin session token cookie from `/api/admin/superadmin/login`.
- Super-admin user and password hash are configured by:
- `SUPERADMIN_USERNAME`
- `SUPERADMIN_PASSWORD_HASH` (bcrypt hash)

Generate a super-admin password hash:

```bash
docker compose run --rm backend python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
```

Generate a strong NextAuth secret:

```bash
openssl rand -base64 32
```

## Runtime Services

- `frontend`: Next.js production server on container port `3000`.
- `backend`: FastAPI + Uvicorn on container port `8000`.
- `worker`: Python process (`worker.main`) heartbeat loop.
- `postgres`: `pgvector/pgvector:pg16`.
- `redis`: `redis:7-alpine`.
- `minio`: object storage and console.

Backend startup runs schema bootstrap from [`backend/app/schema.sql`](/Users/match/New_RAG/backend/app/schema.sql).

## Operational Commands

Validate compose:

```bash
docker compose config
```

Status:

```bash
docker compose ps
```

Logs:

```bash
docker compose logs -f frontend
docker compose logs -f backend
docker compose logs -f worker
```

Rebuild one service:

```bash
docker compose up -d --build frontend
docker compose up -d --build backend
```

## Key Endpoints

User-facing:
- `GET /` assistant UI (frontend).
- `GET /admin` admin UI (frontend).
- `POST /api/ask` frontend ask proxy.

Backend health:
- `GET /health`
- `GET /api/v1/health`

Backend ask:
- `POST /api/v1/ask`

Backend admin:
- `POST /api/v1/admin/ingest/pdf`
- `POST /api/v1/admin/ingest/webpage`
- `POST /api/v1/admin/ingest/webpage/linked`
- `GET /api/v1/admin/documents`
- `DELETE /api/v1/admin/documents/{document_id}`
- `POST /api/v1/admin/documents/{document_id}/reingest`
- `GET /api/v1/admin/docs-sets`
- `DELETE /api/v1/admin/docs-sets/{docs_set_id}`
- `GET /api/v1/admin/discovered-links?source_document_id={id}`
- `GET /api/v1/admin/ask-history`
- `POST /api/v1/admin/superadmin/login`
- `GET /api/v1/admin/superadmin/verify`
- `GET /api/v1/admin/users`
- `POST /api/v1/admin/users/role`

## Example API Calls

PDF ingest:

```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/pdf" \
  -H "Expect:" \
  -H "x-user-email: your-email@company.com" \
  -H "x-user-name: Your Name" \
  -F "file=@Fusion4Broadworks_Product_Description.pdf"
```

Webpage ingest:

```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/webpage" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@company.com" \
  -H "x-user-name: Your Name" \
  -d '{"url":"https://example.com/docs/start","docs_set_name":"Example API Docs"}'
```

Linked-page ingest batch:

```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/admin/ingest/webpage/linked" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@company.com" \
  -H "x-user-name: Your Name" \
  -d '{"source_document_id": 12, "max_pages": 20}'
```

Ask:

```bash
curl -X POST "http://localhost:${BACKEND_PORT:-8000}/api/v1/ask" \
  -H "content-type: application/json" \
  -H "x-user-email: your-email@company.com" \
  -H "x-user-name: Your Name" \
  -H "x-conversation-id: demo-conversation-1" \
  -d '{"question":"Explain the deployment flow and include a diagram."}'
```

## Environment Variables

All runtime configuration is environment-driven. Do not commit real secrets.

### App and Ports

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `APP_ENV` | No | `development` | `production` | Runtime environment label for services. | Not sensitive |
| `FRONTEND_PORT` | No | `3000` | `13000` | Host port mapped to frontend container port `3000`. | Not sensitive |
| `BACKEND_PORT` | No | `8000` | `18000` | Host port mapped to backend container port `8000`. | Not sensitive |
| `POSTGRES_PORT` | No | `5432` | `15432` | Host port mapped to PostgreSQL. | Not sensitive |
| `REDIS_PORT` | No | `6379` | `16379` | Host port mapped to Redis. | Not sensitive |
| `MINIO_PORT` | No | `9000` | `19000` | Host port mapped to MinIO API. | Not sensitive |
| `MINIO_CONSOLE_PORT` | No | `9001` | `19001` | Host port mapped to MinIO console UI. | Not sensitive |
| `APP_URL` | No | `http://localhost:3000` | `https://app.company.com` | Reserved/informational app URL value. | Not sensitive |
| `API_URL` | No | `http://localhost:8000` | `https://api.company.com` | Used by Compose to populate `NEXT_PUBLIC_API_BASE_URL` env (currently not consumed by frontend code path). | Not sensitive |
| `BACKEND_INTERNAL_URL` | No | `http://backend:8000` | `http://backend:8000` | Backend URL used by frontend server routes in Docker network. | Not sensitive |

### Auth and Access Control

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `ALLOWED_GOOGLE_DOMAINS` | Yes | `netaxis.be` | `company.com` | Comma-separated allowed sign-in domains (`*` to allow all in controlled environments). | Not sensitive |
| `ADMIN_EMAILS` | No | empty | `alice@company.com,bob@company.com` | Direct admin allow-list for `/admin` access (without super-admin login). | Not sensitive |
| `GOOGLE_CLIENT_ID` | Yes | empty | `123...apps.googleusercontent.com` | Google OAuth client ID for NextAuth login. | Not sensitive |
| `GOOGLE_CLIENT_SECRET` | Yes | empty | `<secret>` | Google OAuth client secret. | Secret |
| `NEXTAUTH_URL` | Yes | `http://localhost:3000` | `https://app.company.com` | Public callback/base URL for NextAuth. | Not sensitive |
| `NEXTAUTH_SECRET` | Yes | `change_me` | `<random_32+_bytes>` | NextAuth session and token signing key. | Secret |
| `SUPERADMIN_USERNAME` | No | `superadmin` | `superadmin` | Super-admin login username. | Not sensitive |
| `SUPERADMIN_PASSWORD_HASH` | Conditional | empty | `<bcrypt_hash>` | Bcrypt hash for super-admin password. Required only if you want super-admin login enabled. | Secret |
| `SUPERADMIN_SESSION_SECRET` | Yes | `change_me_superadmin_secret` | `<secret>` | HMAC secret used to sign super-admin session tokens. | Secret |
| `SUPERADMIN_SESSION_TTL_SECONDS` | No | `43200` | `14400` | Super-admin session token TTL (seconds). | Not sensitive |
| `PUBLIC_DOCUMENT_DOWNLOADS` | No | `false` | `true` | Reserved flag for public document downloads (not active in current code). | Not sensitive |

### Model and Retrieval

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `ANSWER_PROVIDER` | No | `openai` | `openai` | Provider for answer generation (`openai` implemented). | Not sensitive |
| `VISION_PROVIDER` | No | `openai` | `openai` | Provider for image captioning during ingest (`openai` implemented). | Not sensitive |
| `EMBEDDINGS_PROVIDER` | No | `openai` | `openai` | Provider for embeddings (`openai` implemented). | Not sensitive |
| `ANSWER_MODEL` | No | `gpt-5.2` | `gpt-5.2` | OpenAI Responses model for answer generation. | Not sensitive |
| `VISION_MODEL` | No | `gpt-5.2` | `gpt-5.2` | Vision model used for image captioning. | Not sensitive |
| `EMBEDDINGS_MODEL` | No | `text-embedding-3-large` | `text-embedding-3-large` | Embedding model ID. | Not sensitive |
| `ANSWER_GROUNDING_MODE` | No | `balanced` | `strict` | Grounding strictness for answer synthesis. | Not sensitive |
| `RETRIEVAL_PLANNER_ENABLED` | No | `true` | `false` | Enables LLM query-planner step for retrieval variants. | Not sensitive |
| `RETRIEVAL_SECOND_PASS_ENABLED` | No | `true` | `false` | Enables second retrieval pass when evidence is weak. | Not sensitive |
| `RETRIEVAL_MAX_ROUNDS` | No | `2` | `1` | Max retrieval rounds per ask. | Not sensitive |
| `RETRIEVAL_QUERY_VARIANTS_MAX` | No | `4` | `5` | Max planner query variants in primary pass. | Not sensitive |
| `RETRIEVAL_SECOND_PASS_QUERY_VARIANTS_MAX` | No | `3` | `4` | Max planner query variants in second pass. | Not sensitive |
| `RETRIEVAL_CONTEXT_ROWS_FOR_ANSWER` | No | `20` | `12` | Number of retrieved rows used for answer context. | Not sensitive |
| `RETRIEVAL_FULL_DOC_CONTEXT_ENABLED` | No | `true` | `false` | Enables full-document expansion for top docs after broadened retrieval. | Not sensitive |
| `RETRIEVAL_FULL_DOC_CONTEXT_TOP_DOCS` | No | `2` | `3` | Number of top documents expanded when full-doc context is enabled. | Not sensitive |
| `RETRIEVAL_FULL_DOC_CONTEXT_MAX_CHARS_PER_DOC` | No | `120000` | `80000` | Max chars per expanded document in synthesis context. | Not sensitive |
| `ASK_TOP_K` | No | `6` | `8` | Top chunks/captions selected from retrieval ranking. | Not sensitive |
| `OPENAI_API_KEY` | Conditional | empty | `<secret>` | Required for current implemented provider path (`openai`). | Secret |
| `OPENAI_TIMEOUT_SECONDS` | No | `60` | `60` | Timeout for OpenAI API requests. | Not sensitive |
| `GENERATED_IMAGES_ENABLED` | No | `true` | `false` | Enables generated answer visuals. | Not sensitive |
| `GENERATED_IMAGE_MODEL` | No | `gpt-image-1.5` | `gpt-image-1.5` | OpenAI image model for generated answer visuals. | Not sensitive |
| `GENERATED_IMAGE_SIZE` | No | `1024x1024` | `1536x1024` | Base generated image size. Logic prefers landscape for architecture/flow prompts when square is configured. | Not sensitive |
| `GENERATED_IMAGE_QUALITY` | No | `medium` | `high` | Image quality; visual asks auto-upgrade `medium` to `high` in current logic. | Not sensitive |
| `GENERATED_IMAGE_MAX_PER_ANSWER` | No | `1` | `1` | Max generated visuals returned for one answer. | Not sensitive |
| `OLLAMA_BASE_URL` | Conditional | `http://ollama:11434` | `http://host.docker.internal:11434` | Base URL for future Ollama support. | Not sensitive |

### Web Ingestion

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `WEB_FETCH_TIMEOUT_SECONDS` | No | `20` | `30` | Timeout for webpage fetch requests. | Not sensitive |
| `WEB_INGEST_MAX_CHARS` | No | `120000` | `100000` | Max normalized webpage text chars before chunking. | Not sensitive |
| `WEB_INGEST_MAX_CHUNKS` | No | `120` | `150` | Max chunk entries embedded per webpage ingest. | Not sensitive |
| `WEB_INGEST_MAX_IMAGES` | No | `60` | `80` | Max discovered webpage images downloaded per page. | Not sensitive |
| `WEB_INGEST_USER_AGENT` | No | `ContextForgeBot/1.0` | `ContextForgeBot/1.0 (+https://app.company.com)` | User-Agent for webpage fetches. | Not sensitive |
| `GOOGLE_DELEGATED_BEARER_TOKEN` | No | empty | `<oauth_access_token>` | Optional bearer token for delegated Google host fetches (`docs.google.com`, `drive.google.com`, `sites.google.com`). | Secret |

### Data Stores

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `POSTGRES_DB` | No | `contextforge` | `contextforge` | PostgreSQL DB name (container init). | Not sensitive |
| `POSTGRES_USER` | No | `contextforge` | `contextforge` | PostgreSQL user (container init). | Not sensitive |
| `POSTGRES_PASSWORD` | No | `contextforge` | `<secret>` | PostgreSQL password (container init). | Secret |
| `DATABASE_URL` | Yes | `postgresql://contextforge:contextforge@postgres:5432/contextforge` | `postgresql://user:pass@postgres:5432/db` | Backend/worker PostgreSQL connection string. | Secret |
| `REDIS_URL` | No | `redis://redis:6379/0` | `redis://redis:6379/0` | Redis connection string. | Not sensitive |
| `S3_ENDPOINT` | No | `http://minio:9000` | `http://minio:9000` | Internal S3 endpoint for backend/worker. | Not sensitive |
| `S3_PUBLIC_ENDPOINT` | No | `http://localhost:9000` | `https://storage.company.com` | Endpoint used to generate presigned URLs reachable by user browser. | Not sensitive |
| `S3_ACCESS_KEY` | No | `minioadmin` | `minioadmin` | S3 access key. | Secret |
| `S3_SECRET_KEY` | No | `minioadmin` | `<secret>` | S3 secret key. | Secret |
| `S3_BUCKET_DOCUMENTS` | No | `documents` | `documents` | Bucket for source files/snapshots. | Not sensitive |
| `S3_BUCKET_ASSETS` | No | `assets` | `assets` | Bucket for extracted and generated assets. | Not sensitive |

### Ingestion and Vision Filters

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `IMAGE_MIN_WIDTH` | No | `320` | `320` | Min image width for captioning eligibility. | Not sensitive |
| `IMAGE_MIN_HEIGHT` | No | `320` | `320` | Min image height for captioning eligibility. | Not sensitive |
| `IMAGE_MIN_AREA` | No | `200000` | `200000` | Min image area for captioning eligibility. | Not sensitive |
| `IMAGE_MIN_BYTES` | No | `15000` | `15000` | Min image file size for captioning eligibility. | Not sensitive |
| `IMAGE_MAX_ASPECT_RATIO` | No | `8` | `8` | Max allowed aspect ratio for captioning eligibility. | Not sensitive |
| `IMAGE_MAX_PER_PAGE` | No | `5` | `5` | Max selected images per PDF page for captioning. | Not sensitive |
| `CAPTION_MAX_CHARS` | No | `1200` | `1200` | Max characters per generated caption. | Not sensitive |
| `INGEST_CHUNK_SIZE_CHARS` | No | `1200` | `1500` | Chunk size for text splitting. | Not sensitive |
| `INGEST_CHUNK_OVERLAP_CHARS` | No | `180` | `200` | Overlap between chunks during splitting. | Not sensitive |
| `INGEST_MAX_CHUNKS` | No | `200` | `300` | Max chunk count in PDF ingest pipeline. | Not sensitive |
| `INGEST_MAX_VISION_IMAGES` | No | `0` | `40` | Global cap on captioned images per ingest (`0` means no global cap). | Not sensitive |

### Worker and Reserved Metrics

| Variable | Required | Default | Example | Purpose | Security note |
|---|---|---|---|---|---|
| `ASK_LATENCY_P50_TARGET_MS` | No | `10000` | `10000` | Reserved latency target variable (currently not consumed in code). | Not sensitive |
| `ASK_LATENCY_P95_TARGET_MS` | No | `25000` | `25000` | Reserved latency target variable (currently not consumed in code). | Not sensitive |
| `WORKER_POLL_SECONDS` | No | `5` | `10` | Worker heartbeat/poll interval. | Not sensitive |

## Provider Status

- `openai` answer generation: implemented.
- `openai` embeddings: implemented.
- `openai` vision captioning: implemented.
- `openai` generated visuals (`gpt-image-1.5` default): implemented.
- `ollama` for answers/vision/embeddings: not implemented yet in this codebase.

## Notes and Constraints

- Web ingest blocks private/local network targets for safety.
- Linked-page batch ingest is same-domain only and bounded by `max_pages`.
- Buckets are auto-created when needed by backend.
- Chat sidebar history is browser session storage (`sessionStorage`), not server-persisted thread storage.
- Admin Ask History is persisted in DB (`ask_history`) and is filterable by user in admin UI.
- Keep `BACKEND_INTERNAL_URL=http://backend:8000` in Docker Compose mode even when host `BACKEND_PORT` is changed.
- Do not commit `.env` secrets to git.

## Local Dev (Without Docker, Optional)

Docker Compose is the supported path. If needed, you can run components manually:

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Worker:

```bash
cd worker
pip install -r requirements.txt
python -m worker.main
```

You still need external Postgres (`pgvector`), Redis, and S3-compatible storage configured through env vars.

# ContextForge

ContextForge is an LLM-first knowledge assistant for company documents and approved webpages. It behaves like natural chat, supports multimodal retrieval (text + images), and can generate diagrams when useful.

## Current State
This repository now includes an MVP scaffold:
- `frontend` (Next.js app)
- `backend` (FastAPI API with health endpoints)
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
2. Start the full stack:
```bash
docker compose up -d --build
```
3. Stop and remove containers/volumes:
```bash
docker compose down -v
```

## Service Endpoints (Local)
- Frontend: `http://localhost:3000`
- Backend health: `http://localhost:8000/health`
- Backend API health: `http://localhost:8000/api/v1/health`
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

## Configuration
All runtime configuration must come from environment variables (never hardcode secrets).

### Environment Variable Reference
| Variable | Required | Default | Example | Purpose | Sensitive |
|---|---|---|---|---|---|
| `APP_ENV` | No | `development` | `production` | Runtime mode | No |
| `APP_URL` | No | `http://localhost:3000` | `https://app.company.com` | Public frontend URL | No |
| `API_URL` | No | `http://localhost:8000` | `https://api.company.com` | Public API URL | No |
| `ALLOWED_GOOGLE_DOMAINS` | Yes | `netaxis.be` | `netaxis.be,google.com` | Allowed Google email domains (`*` for all) | No |
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
| `OPENAI_API_KEY` | Conditionally | - | `<secret>` | Required when a provider is `openai` | Yes |
| `OLLAMA_BASE_URL` | Conditionally | `http://ollama:11434` | `http://host.docker.internal:11434` | Required when a provider is `ollama` | No |
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
| `ASK_LATENCY_P50_TARGET_MS` | No | `10000` | `10000` | p50 latency target | No |
| `ASK_LATENCY_P95_TARGET_MS` | No | `25000` | `25000` | p95 latency target | No |
| `WORKER_POLL_SECONDS` | No | `5` | `10` | Worker heartbeat/poll interval | No |

Notes:
- "Conditionally" means required only when selected provider needs it.
- Never commit real secrets. Use placeholders in docs and examples.

## Documentation Governance
- `README.md` is a living operational document.
- Any change to configuration, deployment, runtime behavior, or dependencies must update this file in the same PR.
- Every coding/testing cycle should include a README consistency review.

## Git Workflow
- Use short-lived feature branches from `main`.
- Use Conventional Commits (example: `feat(api): add no-retrieval fallback mode`).
- Keep PRs focused and ensure README + ENV documentation checks are completed.

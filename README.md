# New_RAG

An LLM-first knowledge assistant that behaves like natural chat while using ingested PDFs and webpages as context.

## Status
This repository currently contains product and architecture specifications:
- `definicja_produktu.md`
- `architektura_techniczna.md`
- `ui.md`
- `prd_v2_en.md`

Implementation is planned and will be Dockerized from day one.

## What We Are Building
- Google-authenticated chat app for approved domains only.
- Admin-managed ingestion of PDFs and webpages (public + Google delegated).
- Semantic retrieval over text and image captions.
- Synthesized answers in model's own words, with optional webpage links when useful.
- Confidence percentage in responses.
- Optional diagram generation when useful.

## Deployment (Docker Compose)
Target deployment for v1 is a single `docker-compose.yml` with:
- `frontend`
- `backend`
- `worker`
- `postgres` + `pgvector`
- `redis`
- `minio`

Planned startup command:

```bash
docker compose up -d --build
```

Planned shutdown command:

```bash
docker compose down -v
```

## Configuration
All runtime configuration must come from environment variables (no hardcoded secrets).

### Environment Variable Reference
| Variable | Required | Default | Example | Purpose | Sensitive |
|---|---|---|---|---|---|
| `APP_ENV` | No | `development` | `production` | Runtime mode | No |
| `APP_URL` | No | `http://localhost:3000` | `https://app.company.com` | Public frontend URL | No |
| `API_URL` | No | `http://localhost:8000` | `https://api.company.com` | Public API URL | No |
| `ALLOWED_GOOGLE_DOMAINS` | Yes | `netaxis.be` | `netaxis.be,google.com` | Allowed Google email domains (`*` for all) | No |
| `GOOGLE_CLIENT_ID` | Yes | - | `123...apps.googleusercontent.com` | Google SSO OAuth client id | No |
| `GOOGLE_CLIENT_SECRET` | Yes | - | `<secret>` | Google SSO OAuth client secret | Yes |
| `NEXTAUTH_URL` | Yes | - | `http://localhost:3000` | Auth callback base URL | No |
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
| `OPENAI_API_KEY` | Conditionally | - | `<secret>` | Required when any provider is `openai` | Yes |
| `OLLAMA_BASE_URL` | Conditionally | `http://ollama:11434` | `http://host.docker.internal:11434` | Required when any provider is `ollama` | No |
| `DATABASE_URL` | Yes | - | `postgresql://user:pass@postgres:5432/new_rag` | Main relational + vector metadata DB | Yes |
| `REDIS_URL` | Yes | - | `redis://redis:6379/0` | Queue/cache/rate-limit backend | No |
| `S3_ENDPOINT` | Yes | `http://minio:9000` | `http://minio:9000` | Object storage endpoint | No |
| `S3_ACCESS_KEY` | Yes | - | `minioadmin` | Object storage access key | Yes |
| `S3_SECRET_KEY` | Yes | - | `<secret>` | Object storage secret key | Yes |
| `S3_BUCKET_DOCUMENTS` | Yes | `documents` | `documents` | Bucket for original files | No |
| `S3_BUCKET_ASSETS` | Yes | `assets` | `assets` | Bucket for extracted/generated assets | No |
| `IMAGE_MIN_WIDTH` | No | `320` | `320` | Vision eligibility minimum width | No |
| `IMAGE_MIN_HEIGHT` | No | `320` | `320` | Vision eligibility minimum height | No |
| `IMAGE_MIN_AREA` | No | `200000` | `200000` | Vision eligibility minimum pixel area | No |
| `IMAGE_MIN_BYTES` | No | `15000` | `15000` | Vision eligibility minimum file size | No |
| `IMAGE_MAX_ASPECT_RATIO` | No | `8` | `8` | Vision eligibility max aspect ratio | No |
| `IMAGE_MAX_PER_PAGE` | No | `5` | `5` | Max images captioned per page (largest area first) | No |
| `CAPTION_MAX_CHARS` | No | `1200` | `1200` | Caption max length | No |
| `ASK_LATENCY_P50_TARGET_MS` | No | `10000` | `10000` | p50 response-time target | No |
| `ASK_LATENCY_P95_TARGET_MS` | No | `25000` | `25000` | p95 response-time target | No |

Notes:
- "Conditionally" means required only if selected provider requires it.
- Never commit real secrets; use placeholders in docs and examples.

## Admin-Only Audit History
Each `/ask` interaction is planned to persist:
- asking user identity,
- question and answer,
- confidence and fallback indicators,
- retrieval evidence metadata (chunk ids, document/source ids, webpage urls, image ids).

This global history is admin-only (`admin`/`super_admin`).

## Documentation Governance
- `README.md` is a living operational document.
- Any code change that adds or changes configuration/deployment must update this README in the same change.
- After each coding + testing run, review and update README if needed.

## Git Workflow
- Branch model: `main` + short-lived feature branches.
- Commit style: Conventional Commits (example: `feat(api): add ask fallback policy`).
- Keep PRs focused and include README updates when config/deploy/runtime behavior changes.

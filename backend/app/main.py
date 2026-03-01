import base64
import hashlib
import hmac
import time
from contextlib import asynccontextmanager

import bcrypt
from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from psycopg import Error as PsycopgError

from app.ask_service import (
    AnswerProviderError,
    build_answer,
    ensure_user,
    is_out_of_scope,
    persist_ask_history,
    retrieve_chunks_with_planner,
)
from app.config import settings
from app.db import get_connection, init_db
from app.ingestion_service import IngestionError, ingest_pdf_document
from app.models import (
    AdminAskHistoryResponse,
    AdminDeleteDocsSetResponse,
    AdminDeleteDocumentResponse,
    AdminReingestDocumentResponse,
    AdminDiscoveredLinksResponse,
    AdminDocsSetsResponse,
    AdminDocumentsResponse,
    AdminSetUserRoleRequest,
    AdminSetUserRoleResponse,
    AdminUsersResponse,
    AskRequest,
    AskResponse,
    IngestLinkedPagesRequest,
    IngestLinkedPagesResponse,
    IngestPdfResponse,
    IngestWebRequest,
    IngestWebResponse,
    SuperadminLoginRequest,
    SuperadminLoginResponse,
    SuperadminVerifyResponse,
)
from app.storage import delete_prefix, download_bytes
from app.web_ingestion_service import WebIngestionError, ingest_linked_pages_batch, ingest_webpage_document


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ContextForge Backend", version="0.1.0", lifespan=lifespan)


def _require_auth_email(email: str | None) -> str:
    if not email:
        raise HTTPException(status_code=401, detail="Missing user identity header")
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise HTTPException(status_code=401, detail="Missing user identity header")
    if not settings.is_allowed_google_domain(normalized_email):
        raise HTTPException(status_code=403, detail="User domain is not allowed")
    return normalized_email


def _token_signature(payload: str) -> str:
    return hmac.new(
        settings.superadmin_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_superadmin_token(username: str) -> str:
    issued_at = int(time.time())
    payload = f"{username}:{issued_at}"
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    signature = _token_signature(payload)
    return f"{encoded}.{signature}"


def _is_superadmin_token_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        encoded, signature = token.split(".", 1)
    except ValueError:
        return False

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return False

    expected_signature = _token_signature(payload)
    if not hmac.compare_digest(signature, expected_signature):
        return False

    try:
        username, issued_raw = payload.rsplit(":", 1)
        issued_at = int(issued_raw)
    except ValueError:
        return False

    if username != settings.superadmin_username:
        return False
    if (int(time.time()) - issued_at) > max(settings.superadmin_session_ttl_seconds, 60):
        return False
    return True


def _is_superadmin_password_valid(username: str, password: str) -> bool:
    if username != settings.superadmin_username:
        return False

    configured_hash = settings.superadmin_password_hash.strip()
    if not configured_hash:
        return False

    try:
        return bcrypt.checkpw(password.encode("utf-8"), configured_hash.encode("utf-8"))
    except Exception:
        return False


def _lookup_user_role(conn, email: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM users WHERE lower(email) = lower(%s) LIMIT 1;", (email,))
        row = cur.fetchone()
    if not row:
        return None
    return str(row["role"])


def _require_admin_access(conn, email: str, superadmin_token: str | None) -> str:
    if _is_superadmin_token_valid(superadmin_token):
        return "super_admin"
    if settings.is_admin_email(email):
        return "admin"

    role = _lookup_user_role(conn, email)
    if role in {"admin", "super_admin"}:
        return role
    raise HTTPException(status_code=403, detail="Admin access required")


def _require_superadmin_access(superadmin_token: str | None) -> None:
    if not _is_superadmin_token_valid(superadmin_token):
        raise HTTPException(status_code=403, detail="Super-admin access required")


@app.get("/health")
def health() -> dict:
    db_status = "ok"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
    except PsycopgError:
        db_status = "error"

    return {
        "status": "ok",
        "database": db_status,
        "app_env": settings.app_env,
        "providers": {
            "answer": settings.answer_provider,
            "vision": settings.vision_provider,
            "embeddings": settings.embeddings_provider,
        },
    }


@app.get("/api/v1/health")
def api_health() -> dict:
    return {"status": "ok", "service": "backend"}


@app.post("/api/v1/ask", response_model=AskResponse)
def ask(
    payload: AskRequest,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> AskResponse:
    user_email = _require_auth_email(x_user_email)

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)

        rows, retrieval_trace = retrieve_chunks_with_planner(conn, question, broaden=False)
        retrieval_trace = {"attempts": [{"stage": "primary", **retrieval_trace}]}

        fallback_mode = "none"
        primary_rounds = retrieval_trace["attempts"][0].get("rounds", [])
        if rows and len(primary_rounds) > 1:
            fallback_mode = "broadened_retrieval"
        if not rows:
            if is_out_of_scope(question):
                fallback_mode = "out_of_scope"
            else:
                fallback_mode = "model_knowledge"

        try:
            answer, confidence_percent, grounded, webpage_links, image_urls, generated_image_urls = build_answer(
                question, rows, fallback_mode
            )
        except AnswerProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        retrieval_outcome = "found" if rows else "none"
        persist_ask_history(
            conn,
            user_id=user_id,
            user_email=user_email,
            question=question,
            answer=answer,
            confidence_percent=confidence_percent,
            grounded=grounded,
            fallback_mode=fallback_mode,
            retrieval_outcome=retrieval_outcome,
            rows=rows,
            conversation_id=x_conversation_id,
            retrieval_trace=retrieval_trace,
        )

    return AskResponse(
        answer=answer,
        confidence_percent=confidence_percent,
        grounded=grounded,
        fallback_mode=fallback_mode,
        webpage_links=webpage_links,
        image_urls=image_urls,
        generated_image_urls=generated_image_urls,
    )


@app.post("/api/v1/admin/superadmin/login", response_model=SuperadminLoginResponse)
def superadmin_login(
    payload: SuperadminLoginRequest,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
) -> SuperadminLoginResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)

    if not _is_superadmin_password_valid(payload.username.strip(), payload.password):
        raise HTTPException(status_code=401, detail="Invalid super-admin credentials")

    token = _issue_superadmin_token(settings.superadmin_username)
    return SuperadminLoginResponse(
        token=token,
        expires_in_seconds=max(settings.superadmin_session_ttl_seconds, 60),
        role="super_admin",
    )


@app.get("/api/v1/admin/superadmin/verify", response_model=SuperadminVerifyResponse)
def superadmin_verify(
    x_user_email: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> SuperadminVerifyResponse:
    _require_auth_email(x_user_email)
    if not _is_superadmin_token_valid(x_superadmin_token):
        raise HTTPException(status_code=401, detail="Invalid or expired super-admin session")
    return SuperadminVerifyResponse(valid=True, role="super_admin")


@app.post("/api/v1/admin/ingest/pdf", response_model=IngestPdfResponse)
def ingest_pdf(
    file: UploadFile = File(...),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> IngestPdfResponse:
    user_email = _require_auth_email(x_user_email)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        try:
            result = ingest_pdf_document(
                conn,
                user_id=user_id,
                source_name=file.filename,
                pdf_bytes=file_bytes,
            )
        except IngestionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return IngestPdfResponse(**result)


@app.post("/api/v1/admin/ingest/webpage", response_model=IngestWebResponse)
def ingest_webpage(
    payload: IngestWebRequest,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> IngestWebResponse:
    user_email = _require_auth_email(x_user_email)

    source_url = payload.url.strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        try:
            result = ingest_webpage_document(
                conn,
                user_id=user_id,
                source_url=source_url,
                docs_set_id=payload.docs_set_id,
                docs_set_name=payload.docs_set_name,
                parent_document_id=payload.parent_document_id,
                from_discovered_link_id=payload.discovered_link_id,
            )
        except WebIngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return IngestWebResponse(**{k: v for k, v in result.items() if k != "reused_existing"})


@app.post("/api/v1/admin/ingest/webpage/linked", response_model=IngestLinkedPagesResponse)
def ingest_linked_webpages(
    payload: IngestLinkedPagesRequest,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> IngestLinkedPagesResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        try:
            result = ingest_linked_pages_batch(
                conn,
                user_id=user_id,
                source_document_id=payload.source_document_id,
                max_pages=payload.max_pages,
            )
        except WebIngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return IngestLinkedPagesResponse(**result)


@app.get("/api/v1/admin/documents", response_model=AdminDocumentsResponse)
def list_documents(
    limit: int = Query(default=50, ge=1, le=200),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminDocumentsResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  d.id,
                  d.source_type,
                  d.source_name,
                  d.source_url,
                  d.source_storage_key,
                  d.source_parent_document_id,
                  d.docs_set_id,
                  ds.name AS docs_set_name,
                  d.status,
                  d.text_chunk_count,
                  d.image_count,
                  d.created_at,
                  u.email AS created_by_email
                FROM documents d
                LEFT JOIN docs_sets ds ON ds.id = d.docs_set_id
                LEFT JOIN users u ON u.id = d.created_by
                ORDER BY d.id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return AdminDocumentsResponse(documents=rows)


@app.delete("/api/v1/admin/documents/{document_id}", response_model=AdminDeleteDocumentResponse)
def delete_document(
    document_id: int,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminDeleteDocumentResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE id = %s;", (document_id,))
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        prefix = f"documents/{document_id}/"
        try:
            delete_prefix(bucket_name=settings.s3_bucket_documents, prefix=prefix)
            delete_prefix(bucket_name=settings.s3_bucket_assets, prefix=prefix)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete stored document assets: {exc}") from exc

        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s;", (document_id,))
        conn.commit()

    return AdminDeleteDocumentResponse(document_id=document_id, status="deleted")


@app.post("/api/v1/admin/documents/{document_id}/reingest", response_model=AdminReingestDocumentResponse)
def reingest_document(
    document_id: int,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminReingestDocumentResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id,
                  source_type,
                  source_name,
                  source_url,
                  source_storage_key,
                  docs_set_id,
                  source_parent_document_id
                FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            )
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        source_type = str(existing["source_type"])
        source_name = str(existing["source_name"])
        source_url = existing.get("source_url")
        source_storage_key = existing.get("source_storage_key")
        docs_set_id = existing.get("docs_set_id")
        source_parent_document_id = existing.get("source_parent_document_id")

        if source_type == "pdf":
            if not source_storage_key:
                raise HTTPException(status_code=400, detail="Cannot re-ingest PDF without source storage key")
            try:
                pdf_bytes = download_bytes(bucket_name=settings.s3_bucket_documents, key=str(source_storage_key))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Failed to load stored PDF: {exc}") from exc
            if not pdf_bytes:
                raise HTTPException(status_code=500, detail="Stored PDF bytes are empty")
        elif source_type == "web":
            if not source_url:
                raise HTTPException(status_code=400, detail="Cannot re-ingest webpage without source URL")
            pdf_bytes = b""
        else:
            raise HTTPException(status_code=400, detail="Unsupported source type for re-ingest")

        try:
            if source_type == "pdf":
                result = ingest_pdf_document(
                    conn,
                    user_id=user_id,
                    source_name=source_name,
                    pdf_bytes=pdf_bytes,
                )
            else:
                result = ingest_webpage_document(
                    conn,
                    user_id=user_id,
                    source_url=str(source_url),
                    docs_set_id=int(docs_set_id) if docs_set_id is not None else None,
                    parent_document_id=int(source_parent_document_id) if source_parent_document_id is not None else None,
                    force_reingest=True,
                )
        except (IngestionError, WebIngestionError) as exc:
            raise HTTPException(status_code=500, detail=f"Re-ingest failed: {exc}") from exc

        old_prefix = f"documents/{document_id}/"
        try:
            delete_prefix(bucket_name=settings.s3_bucket_documents, prefix=old_prefix)
            delete_prefix(bucket_name=settings.s3_bucket_assets, prefix=old_prefix)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to remove old assets after re-ingest: {exc}") from exc

        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s;", (document_id,))
        conn.commit()

    return AdminReingestDocumentResponse(
        old_document_id=document_id,
        new_document_id=int(result["document_id"]),
        status="reingested",
    )


@app.get("/api/v1/admin/ask-history", response_model=AdminAskHistoryResponse)
def list_ask_history(
    limit: int = Query(default=50, ge=1, le=200),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminAskHistoryResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id,
                  created_at,
                  user_email,
                  question,
                  fallback_mode,
                  retrieval_outcome,
                  confidence_percent,
                  grounded,
                  documents_used,
                  chunks_used,
                  images_used,
                  webpage_links,
                  evidence
                FROM ask_history
                ORDER BY id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return AdminAskHistoryResponse(history=rows)


@app.get("/api/v1/admin/users", response_model=AdminUsersResponse)
def list_users(
    limit: int = Query(default=200, ge=1, le=1000),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminUsersResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_superadmin_access(x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, email, full_name, role, created_at, last_login
                FROM users
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return AdminUsersResponse(users=rows)


@app.post("/api/v1/admin/users/role", response_model=AdminSetUserRoleResponse)
def set_user_role(
    payload: AdminSetUserRoleRequest,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminSetUserRoleResponse:
    user_email = _require_auth_email(x_user_email)
    target_email = payload.email.strip().lower()
    if "@" not in target_email:
        raise HTTPException(status_code=400, detail="Invalid target email")

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_superadmin_access(x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, full_name, role, created_at, last_login)
                VALUES (%s, NULL, %s, NOW(), NOW())
                ON CONFLICT (email)
                DO UPDATE SET role = EXCLUDED.role
                RETURNING email, role;
                """,
                (target_email, payload.role),
            )
            row = cur.fetchone()
        conn.commit()

    return AdminSetUserRoleResponse(email=row["email"], role=row["role"], status="updated")


@app.get("/api/v1/admin/docs-sets", response_model=AdminDocsSetsResponse)
def list_docs_sets(
    limit: int = Query(default=100, ge=1, le=500),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminDocsSetsResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  ds.id,
                  ds.name,
                  ds.root_url,
                  ds.source_type,
                  ds.created_at,
                  u.email AS created_by_email,
                  COUNT(d.id)::integer AS document_count
                FROM docs_sets ds
                LEFT JOIN users u ON u.id = ds.created_by
                LEFT JOIN documents d ON d.docs_set_id = ds.id
                GROUP BY ds.id, u.email
                ORDER BY ds.id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return AdminDocsSetsResponse(docs_sets=rows)


@app.delete("/api/v1/admin/docs-sets/{docs_set_id}", response_model=AdminDeleteDocsSetResponse)
def delete_docs_set(
    docs_set_id: int,
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminDeleteDocsSetResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM docs_sets WHERE id = %s;", (docs_set_id,))
            existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Docs set not found")

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE docs_set_id = %s;", (docs_set_id,))
            doc_rows = cur.fetchall()
        document_ids = [int(row["id"]) for row in doc_rows]

        try:
            for document_id in document_ids:
                prefix = f"documents/{document_id}/"
                delete_prefix(bucket_name=settings.s3_bucket_documents, prefix=prefix)
                delete_prefix(bucket_name=settings.s3_bucket_assets, prefix=prefix)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete docs-set assets: {exc}") from exc

        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE docs_set_id = %s;", (docs_set_id,))
            cur.execute("DELETE FROM docs_sets WHERE id = %s;", (docs_set_id,))
        conn.commit()

    return AdminDeleteDocsSetResponse(
        docs_set_id=docs_set_id,
        deleted_documents_count=len(document_ids),
        status="deleted",
    )


@app.get("/api/v1/admin/discovered-links", response_model=AdminDiscoveredLinksResponse)
def list_discovered_links(
    source_document_id: int = Query(..., ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
    x_superadmin_token: str | None = Header(default=None),
) -> AdminDiscoveredLinksResponse:
    user_email = _require_auth_email(x_user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
        _require_admin_access(conn, user_email, x_superadmin_token)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id,
                  source_document_id,
                  docs_set_id,
                  url,
                  normalized_url,
                  link_text,
                  same_domain,
                  status,
                  ingested_document_id,
                  last_error,
                  created_at,
                  updated_at
                FROM web_discovered_links
                WHERE source_document_id = %s
                ORDER BY id ASC
                LIMIT %s;
                """,
                (source_document_id, limit),
            )
            rows = cur.fetchall()
    return AdminDiscoveredLinksResponse(links=rows)

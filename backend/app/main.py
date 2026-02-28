from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from psycopg import Error as PsycopgError

from app.ask_service import (
    AnswerProviderError,
    build_answer,
    ensure_user,
    is_out_of_scope,
    persist_ask_history,
    retrieve_chunks,
)
from app.config import settings
from app.db import get_connection, init_db
from app.ingestion_service import IngestionError, ingest_pdf_document
from app.models import (
    AdminAskHistoryResponse,
    AdminDeleteDocsSetResponse,
    AdminDeleteDocumentResponse,
    AdminDiscoveredLinksResponse,
    AdminDocsSetsResponse,
    AdminDocumentsResponse,
    AskRequest,
    AskResponse,
    IngestLinkedPagesRequest,
    IngestLinkedPagesResponse,
    IngestPdfResponse,
    IngestWebRequest,
    IngestWebResponse,
)
from app.storage import delete_prefix
from app.web_ingestion_service import WebIngestionError, ingest_linked_pages_batch, ingest_webpage_document


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ContextForge Backend", version="0.1.0", lifespan=lifespan)


def _require_auth_email(email: str | None) -> str:
    if not email:
        raise HTTPException(status_code=401, detail="Missing user identity header")
    if not settings.is_allowed_google_domain(email):
        raise HTTPException(status_code=403, detail="User domain is not allowed")
    return email


def _require_admin_email(email: str) -> None:
    if not settings.is_admin_email(email):
        raise HTTPException(status_code=403, detail="Admin access required")


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

        rows = retrieve_chunks(conn, question, broaden=False)
        fallback_mode = "none"

        if not rows:
            rows = retrieve_chunks(conn, question, broaden=True)
            if rows:
                fallback_mode = "broadened_retrieval"
            elif is_out_of_scope(question):
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


@app.post("/api/v1/admin/ingest/pdf", response_model=IngestPdfResponse)
def ingest_pdf(
    file: UploadFile = File(...),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
) -> IngestPdfResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
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
) -> IngestWebResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    source_url = payload.url.strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
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
) -> IngestLinkedPagesResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        user_id = ensure_user(conn, user_email, x_user_name)
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
) -> AdminDocumentsResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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
) -> AdminDeleteDocumentResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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


@app.get("/api/v1/admin/ask-history", response_model=AdminAskHistoryResponse)
def list_ask_history(
    limit: int = Query(default=50, ge=1, le=200),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
) -> AdminAskHistoryResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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


@app.get("/api/v1/admin/docs-sets", response_model=AdminDocsSetsResponse)
def list_docs_sets(
    limit: int = Query(default=100, ge=1, le=500),
    x_user_email: str | None = Header(default=None),
    x_user_name: str | None = Header(default=None),
) -> AdminDocsSetsResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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
) -> AdminDeleteDocsSetResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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
) -> AdminDiscoveredLinksResponse:
    user_email = _require_auth_email(x_user_email)
    _require_admin_email(user_email)

    with get_connection() as conn:
        ensure_user(conn, user_email, x_user_name)
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

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
    AdminDeleteDocumentResponse,
    AdminDocumentsResponse,
    AskRequest,
    AskResponse,
    IngestPdfResponse,
)
from app.storage import delete_prefix


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
            answer, confidence_percent, grounded, webpage_links, image_urls = build_answer(
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
                  d.status,
                  d.text_chunk_count,
                  d.image_count,
                  d.created_at,
                  u.email AS created_by_email
                FROM documents d
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

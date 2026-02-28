from io import BytesIO
from typing import Any
from uuid import uuid4

from pypdf import PdfReader

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import OpenAIClientError, embed_texts
from app.storage import ensure_bucket, upload_bytes


class IngestionError(Exception):
    """Raised for ingestion pipeline errors."""


def _chunk_text(text: str) -> list[str]:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    size = max(settings.ingest_chunk_size_chars, 200)
    overlap = max(min(settings.ingest_chunk_overlap_chars, size - 50), 0)
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        start = end - overlap
    return chunks


def _extract_pdf_text_chunks(pdf_bytes: bytes) -> tuple[int, list[dict[str, Any]]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    results: list[dict[str, Any]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk in _chunk_text(text):
            results.append(
                {
                    "page_start": page_index,
                    "page_end": page_index,
                    "text": chunk,
                }
            )

    return page_count, results


def _embed_chunks(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    if settings.embeddings_provider.lower().strip() != "openai":
        raise IngestionError(
            "Only EMBEDDINGS_PROVIDER=openai is implemented for ingestion at this stage."
        )

    batch_size = 32
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            vectors.extend(embed_texts(batch, model=settings.embeddings_model))
        except OpenAIClientError as exc:
            raise IngestionError(str(exc)) from exc

    return vectors


def ingest_pdf_document(
    conn,
    *,
    user_id: str,
    source_name: str,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    if not pdf_bytes:
        raise IngestionError("Uploaded PDF is empty")

    document_id: int | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (source_type, source_name, source_url, created_by, status)
            VALUES ('pdf', %s, NULL, %s, 'processing')
            RETURNING id;
            """,
            (source_name, user_id),
        )
        row = cur.fetchone()
        document_id = int(row["id"])
    conn.commit()

    try:
        document_key = f"documents/{document_id}/{uuid4()}-{source_name}"
        ensure_bucket(settings.s3_bucket_documents)
        upload_bytes(
            bucket_name=settings.s3_bucket_documents,
            key=document_key,
            data=pdf_bytes,
            content_type="application/pdf",
        )

        page_count, chunks = _extract_pdf_text_chunks(pdf_bytes)
        if settings.ingest_max_chunks > 0:
            chunks = chunks[: settings.ingest_max_chunks]
        chunk_texts = [chunk["text"] for chunk in chunks]
        vectors = _embed_chunks(chunk_texts) if chunk_texts else []

        with conn.cursor() as cur:
            for chunk, vector in zip(chunks, vectors):
                cur.execute(
                    """
                    INSERT INTO text_chunks (document_id, page_start, page_end, text, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector);
                    """,
                    (
                        document_id,
                        chunk["page_start"],
                        chunk["page_end"],
                        chunk["text"],
                        embedding_to_vector_literal(vector),
                    ),
                )

            cur.execute(
                """
                UPDATE documents
                SET
                  status = 'ready',
                  text_chunk_count = %s,
                  image_count = 0
                WHERE id = %s;
                """,
                (len(chunks), document_id),
            )
        conn.commit()

        return {
            "document_id": document_id,
            "source_name": source_name,
            "status": "ready",
            "page_count": page_count,
            "text_chunk_count": len(chunks),
            "image_count": 0,
            "storage_key": document_key,
        }
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status = 'failed' WHERE id = %s;",
                (document_id,),
            )
        conn.commit()
        if isinstance(exc, IngestionError):
            raise
        raise IngestionError(str(exc)) from exc

from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import OpenAIClientError, embed_texts, generate_image_caption
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


def _format_to_mime(fmt: str | None) -> tuple[str, str]:
    normalized = (fmt or "").lower()
    if normalized == "jpeg":
        return "image/jpeg", "jpg"
    if normalized in {"jpg", "png", "webp", "gif", "bmp", "tiff"}:
        if normalized == "jpg":
            normalized = "jpeg"
        return f"image/{normalized}", "jpg" if normalized == "jpeg" else normalized
    return "application/octet-stream", "bin"


def _extract_pdf_content(pdf_bytes: bytes) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    text_chunks: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk in _chunk_text(text):
            text_chunks.append(
                {
                    "page_start": page_index,
                    "page_end": page_index,
                    "text": chunk,
                }
            )

        page_images = list(page.images)
        for image_index, image_file in enumerate(page_images, start=1):
            image_bytes = getattr(image_file, "data", b"")
            if not image_bytes:
                continue

            width = 0
            height = 0
            image_format: str | None = None
            try:
                with Image.open(BytesIO(image_bytes)) as img:
                    width, height = img.size
                    image_format = img.format
            except (UnidentifiedImageError, OSError):
                # Keep unknown images as stored assets, but they will not pass Vision policy.
                pass

            mime_type, extension = _format_to_mime(image_format)
            image_name = getattr(image_file, "name", f"image_{image_index}")

            images.append(
                {
                    "page_number": page_index,
                    "image_index": image_index,
                    "name": image_name,
                    "bytes": image_bytes,
                    "file_bytes": len(image_bytes),
                    "width": width,
                    "height": height,
                    "mime_type": mime_type,
                    "extension": extension,
                }
            )

    return page_count, text_chunks, images


def _embed_texts_for_ingest(texts: list[str]) -> list[list[float]]:
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


def _passes_vision_policy(image: dict[str, Any]) -> bool:
    width = int(image.get("width", 0))
    height = int(image.get("height", 0))
    file_bytes = int(image.get("file_bytes", 0))
    if width < settings.image_min_width:
        return False
    if height < settings.image_min_height:
        return False

    area = width * height
    if area < settings.image_min_area:
        return False

    if file_bytes < settings.image_min_bytes:
        return False

    shorter = max(min(width, height), 1)
    longer = max(width, height)
    aspect_ratio = longer / shorter
    if aspect_ratio > settings.image_max_aspect_ratio:
        return False

    return True


def _select_images_for_captioning(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for image in images:
        if not _passes_vision_policy(image):
            continue
        grouped.setdefault(int(image["page_number"]), []).append(image)

    selected: list[dict[str, Any]] = []
    for page in sorted(grouped.keys()):
        candidates = grouped[page]
        candidates.sort(key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)), reverse=True)
        selected.extend(candidates[: max(settings.image_max_per_page, 1)])

    if settings.ingest_max_vision_images > 0:
        selected = selected[: settings.ingest_max_vision_images]

    return selected


def _generate_captions_for_images(images: list[dict[str, Any]]) -> list[str]:
    if not images:
        return []

    provider = settings.vision_provider.lower().strip()
    if provider != "openai":
        raise IngestionError("Only VISION_PROVIDER=openai is implemented for ingestion at this stage.")

    captions: list[str] = []
    for image in images:
        try:
            caption = generate_image_caption(
                model=settings.vision_model,
                image_bytes=image["bytes"],
                mime_type=image["mime_type"],
                max_chars=settings.caption_max_chars,
            )
        except OpenAIClientError as exc:
            raise IngestionError(str(exc)) from exc
        captions.append(caption)
    return captions


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
        ensure_bucket(settings.s3_bucket_assets)

        upload_bytes(
            bucket_name=settings.s3_bucket_documents,
            key=document_key,
            data=pdf_bytes,
            content_type="application/pdf",
        )

        page_count, chunks, images = _extract_pdf_content(pdf_bytes)
        if settings.ingest_max_chunks > 0:
            chunks = chunks[: settings.ingest_max_chunks]

        chunk_texts = [chunk["text"] for chunk in chunks]
        chunk_vectors = _embed_texts_for_ingest(chunk_texts) if chunk_texts else []

        image_rows_with_ids: list[dict[str, Any]] = []
        with conn.cursor() as cur:
            for image in images:
                storage_key = (
                    f"documents/{document_id}/pages/{image['page_number']}/"
                    f"images/{image['image_index']}.{image['extension']}"
                )
                upload_bytes(
                    bucket_name=settings.s3_bucket_assets,
                    key=storage_key,
                    data=image["bytes"],
                    content_type=image["mime_type"],
                )

                cur.execute(
                    """
                    INSERT INTO document_images (
                      document_id,
                      page_number,
                      image_index,
                      storage_key,
                      mime_type,
                      file_bytes,
                      width,
                      height
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        document_id,
                        image["page_number"],
                        image["image_index"],
                        storage_key,
                        image["mime_type"],
                        image["file_bytes"],
                        image["width"],
                        image["height"],
                    ),
                )
                image_id = int(cur.fetchone()["id"])
                image_rows_with_ids.append(
                    {
                        **image,
                        "image_id": image_id,
                        "storage_key": storage_key,
                    }
                )

            for chunk, vector in zip(chunks, chunk_vectors):
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

        images_for_caption = _select_images_for_captioning(image_rows_with_ids)
        captions = _generate_captions_for_images(images_for_caption) if images_for_caption else []
        caption_vectors = _embed_texts_for_ingest(captions) if captions else []

        with conn.cursor() as cur:
            for image, caption, vector in zip(images_for_caption, captions, caption_vectors):
                cur.execute(
                    """
                    INSERT INTO image_captions (image_id, caption_text, embedding, provider, model)
                    VALUES (%s, %s, %s::vector, %s, %s);
                    """,
                    (
                        image["image_id"],
                        caption,
                        embedding_to_vector_literal(vector),
                        settings.vision_provider,
                        settings.vision_model,
                    ),
                )

            cur.execute(
                """
                UPDATE documents
                SET
                  status = 'ready',
                  source_storage_key = %s,
                  text_chunk_count = %s,
                  image_count = %s
                WHERE id = %s;
                """,
                (document_key, len(chunks), len(images), document_id),
            )
        conn.commit()

        return {
            "document_id": document_id,
            "source_name": source_name,
            "status": "ready",
            "page_count": page_count,
            "text_chunk_count": len(chunks),
            "image_count": len(images),
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

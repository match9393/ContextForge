import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import OpenAIClientError, embed_texts


class WebIngestionError(Exception):
    """Raised for webpage ingestion pipeline errors."""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignore_depth = 0
        self._title_depth = 0
        self._texts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth += 1
        if normalized == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
        if normalized == "title" and self._title_depth > 0:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth > 0:
            cleaned_title = " ".join(data.split()).strip()
            if cleaned_title:
                self._title_parts.append(cleaned_title)

        if self._ignore_depth > 0:
            return
        cleaned = " ".join(data.split()).strip()
        if cleaned:
            self._texts.append(cleaned)

    @property
    def page_title(self) -> str:
        return " ".join(self._title_parts).strip()

    @property
    def page_text(self) -> str:
        return " ".join(self._texts).strip()


def _is_google_delegated_host(hostname: str) -> bool:
    google_hosts = {"docs.google.com", "drive.google.com", "sites.google.com"}
    return hostname.lower() in google_hosts


def _assert_public_host(hostname: str) -> None:
    try:
        addr_info = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebIngestionError(f"Could not resolve host: {hostname}") from exc

    for item in addr_info:
        ip_text = item[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            raise WebIngestionError("Only publicly reachable webpages are allowed for ingestion.")


def _extract_charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        candidate = part.strip().lower()
        if candidate.startswith("charset="):
            return candidate.split("=", 1)[1].strip() or "utf-8"
    return "utf-8"


def _fetch_webpage(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise WebIngestionError("Only http/https URLs are supported.")
    if not parsed.hostname:
        raise WebIngestionError("URL must include a valid hostname.")

    _assert_public_host(parsed.hostname)

    headers: dict[str, str] = {
        "User-Agent": settings.web_ingest_user_agent,
        "Accept": "text/html,application/xhtml+xml,text/plain",
    }
    if _is_google_delegated_host(parsed.hostname) and settings.google_delegated_bearer_token:
        headers["Authorization"] = f"Bearer {settings.google_delegated_bearer_token}"

    request = Request(url=url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=settings.web_fetch_timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise WebIngestionError(f"Unsupported content type: {content_type or 'unknown'}")
            payload = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise WebIngestionError(
                "Webpage is not publicly accessible. In v1, ingest public URLs or configure Google delegated token."
            ) from exc
        raise WebIngestionError(f"Web request failed with status {exc.code}.") from exc
    except URLError as exc:
        raise WebIngestionError(f"Web request failed: {exc.reason}") from exc

    max_bytes = max(settings.web_ingest_max_chars, 5000) * 4
    payload = payload[:max_bytes]
    decoded = payload.decode(_extract_charset(content_type), errors="ignore")

    if "text/plain" in content_type:
        normalized = " ".join(decoded.split()).strip()
        if not normalized:
            raise WebIngestionError("No usable text found on webpage.")
        return parsed.hostname, normalized

    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    text = parser.page_text
    if not text:
        raise WebIngestionError("No usable text found on webpage.")
    title = parser.page_title or parsed.hostname
    return title, text


def _chunk_text(text: str) -> list[str]:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []

    max_chars = max(settings.web_ingest_max_chars, 5000)
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars]

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

    if settings.web_ingest_max_chunks > 0:
        chunks = chunks[: settings.web_ingest_max_chunks]
    return chunks


def _embed_texts_for_ingest(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if settings.embeddings_provider.lower().strip() != "openai":
        raise WebIngestionError("Only EMBEDDINGS_PROVIDER=openai is implemented for webpage ingestion.")

    vectors: list[list[float]] = []
    batch_size = 32
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            vectors.extend(embed_texts(batch, model=settings.embeddings_model))
        except OpenAIClientError as exc:
            raise WebIngestionError(str(exc)) from exc
    return vectors


def ingest_webpage_document(
    conn,
    *,
    user_id: str,
    source_url: str,
) -> dict[str, Any]:
    source_name, page_text = _fetch_webpage(source_url)
    chunks = _chunk_text(page_text)
    if not chunks:
        raise WebIngestionError("Webpage text is too short after normalization.")
    vectors = _embed_texts_for_ingest(chunks)

    document_id: int | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (source_type, source_name, source_url, created_by, status)
            VALUES ('web', %s, %s, %s, 'processing')
            RETURNING id;
            """,
            (source_name, source_url, user_id),
        )
        row = cur.fetchone()
        document_id = int(row["id"])
    conn.commit()

    try:
        with conn.cursor() as cur:
            for chunk, vector in zip(chunks, vectors):
                cur.execute(
                    """
                    INSERT INTO text_chunks (document_id, page_start, page_end, text, embedding)
                    VALUES (%s, NULL, NULL, %s, %s::vector);
                    """,
                    (document_id, chunk, embedding_to_vector_literal(vector)),
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
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("UPDATE documents SET status = 'failed' WHERE id = %s;", (document_id,))
        conn.commit()
        raise WebIngestionError(str(exc)) from exc

    return {
        "document_id": document_id,
        "source_name": source_name,
        "source_url": source_url,
        "status": "ready",
        "text_chunk_count": len(chunks),
    }

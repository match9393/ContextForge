import ipaddress
import json
import re
import socket
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from uuid import uuid4

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import OpenAIClientError, embed_texts, generate_image_caption
from app.storage import ensure_bucket, upload_bytes

NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")


class WebIngestionError(Exception):
    """Raised for webpage ingestion pipeline errors."""


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise WebIngestionError("Only http/https URLs are supported.")
    if not parsed.hostname:
        raise WebIngestionError("URL must include a valid hostname.")

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query_params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    query_params.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_params, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def _is_google_delegated_host(hostname: str) -> bool:
    google_hosts = {"docs.google.com", "drive.google.com", "sites.google.com"}
    return hostname.lower() in google_hosts


def _is_same_domain(base_host: str, candidate_host: str) -> bool:
    base = base_host.lower()
    candidate = candidate_host.lower()
    return candidate == base or candidate.endswith(f".{base}")


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


def _request_headers(hostname: str, accept: str) -> dict[str, str]:
    headers: dict[str, str] = {
        "User-Agent": settings.web_ingest_user_agent,
        "Accept": accept,
    }
    if _is_google_delegated_host(hostname) and settings.google_delegated_bearer_token:
        headers["Authorization"] = f"Bearer {settings.google_delegated_bearer_token}"
    return headers


def _fetch_url_bytes(url: str, *, accept: str, max_bytes: int) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise WebIngestionError("URL must include a valid hostname.")
    _assert_public_host(parsed.hostname)

    request = Request(url=url, method="GET", headers=_request_headers(parsed.hostname, accept))
    try:
        with urlopen(request, timeout=settings.web_fetch_timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise WebIngestionError(
                "Webpage is not publicly accessible. In v1, ingest public URLs or configure Google delegated token."
            ) from exc
        raise WebIngestionError(f"Web request failed with status {exc.code}.") from exc
    except URLError as exc:
        raise WebIngestionError(f"Web request failed: {exc.reason}") from exc

    if len(payload) > max_bytes:
        raise WebIngestionError(f"Fetched content exceeds size limit ({max_bytes} bytes).")
    return payload, content_type.lower()


def _extract_numeric_values(value: str) -> list[float]:
    matches = NUMBER_PATTERN.findall(value)
    numbers: list[float] = []
    for raw in matches:
        normalized = raw.replace(",", "")
        try:
            numbers.append(float(normalized))
        except ValueError:
            continue
    return numbers


def _table_chunk_entries(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_entries: list[dict[str, Any]] = []
    row_entries: list[dict[str, Any]] = []

    for table_index, table in enumerate(soup.find_all("table"), start=1):
        parsed_rows: list[list[str]] = []
        header_row: list[str] | None = None

        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            values = [" ".join(cell.get_text(" ", strip=True).split()) for cell in cells]
            if not any(values):
                continue
            parsed_rows.append(values)
            if header_row is None and tr.find_all("th"):
                header_row = values

        if not parsed_rows:
            continue

        headers = header_row or parsed_rows[0]
        normalized_headers = [value if value else f"column_{idx + 1}" for idx, value in enumerate(headers)]
        data_rows = parsed_rows[1:] if parsed_rows else []

        sample_rows = data_rows[:2]
        summary_text = (
            f"Table {table_index}: columns={len(normalized_headers)}, rows={len(data_rows)}. "
            f"Headers: {' | '.join(normalized_headers)}."
        )
        if sample_rows:
            rendered_samples: list[str] = []
            for row in sample_rows:
                row_pairs = []
                for idx, value in enumerate(row):
                    column = normalized_headers[idx] if idx < len(normalized_headers) else f"column_{idx + 1}"
                    row_pairs.append(f"{column}={value}")
                rendered_samples.append("; ".join(row_pairs))
            summary_text += f" Sample rows: {' || '.join(rendered_samples)}."

        summary_entries.append(
            {
                "chunk_type": "table_summary",
                "text": summary_text[:2000],
                "chunk_meta": {
                    "table_index": table_index,
                    "column_count": len(normalized_headers),
                    "row_count": len(data_rows),
                    "headers": normalized_headers[:50],
                },
            }
        )

        for row_index, row in enumerate(data_rows, start=1):
            row_headers = normalized_headers.copy()
            if len(row) > len(row_headers):
                for idx in range(len(row_headers), len(row)):
                    row_headers.append(f"column_{idx + 1}")

            pairs: list[str] = []
            numeric_values: dict[str, Any] = {}
            for idx, value in enumerate(row):
                key = row_headers[idx]
                pairs.append(f"{key}={value}")
                numbers = _extract_numeric_values(value)
                if numbers:
                    numeric_values[key] = numbers[0] if len(numbers) == 1 else numbers

            row_text = f"Table {table_index} row {row_index}: " + "; ".join(pairs)
            if numeric_values:
                row_text += f" | numeric_values={json.dumps(numeric_values, ensure_ascii=True)}"

            row_entries.append(
                {
                    "chunk_type": "table_row",
                    "text": row_text[:2200],
                    "chunk_meta": {
                        "table_index": table_index,
                        "row_index": row_index,
                        "headers": row_headers[:50],
                        "numeric_values": numeric_values,
                    },
                }
            )

    return summary_entries, row_entries


def _discover_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    parsed_base = urlparse(base_url)
    base_host = parsed_base.hostname or ""
    dedupe: set[str] = set()
    results: list[dict[str, Any]] = []

    for anchor in soup.find_all("a", href=True):
        raw_href = (anchor.get("href") or "").strip()
        if not raw_href or raw_href.startswith("#"):
            continue
        resolved = urljoin(base_url, raw_href)
        try:
            normalized = normalize_url(resolved)
        except WebIngestionError:
            continue
        if normalized in dedupe:
            continue
        dedupe.add(normalized)

        parsed = urlparse(normalized)
        hostname = parsed.hostname or ""
        link_text = " ".join(anchor.get_text(" ", strip=True).split()) or None
        results.append(
            {
                "url": normalized,
                "normalized_url": normalized,
                "link_text": link_text,
                "same_domain": _is_same_domain(base_host, hostname),
            }
        )
        if len(results) >= 500:
            break

    return results


def _discover_image_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    dedupe: set[str] = set()
    urls: list[str] = []
    for img in soup.find_all("img"):
        raw_src = (img.get("src") or img.get("data-src") or "").strip()
        if not raw_src:
            srcset = (img.get("srcset") or "").strip()
            if srcset:
                raw_src = srcset.split(",")[0].strip().split(" ")[0]
        if not raw_src:
            continue
        if raw_src.startswith("data:") or raw_src.startswith("javascript:"):
            continue

        resolved = urljoin(base_url, raw_src)
        try:
            normalized = normalize_url(resolved)
        except WebIngestionError:
            continue
        if normalized in dedupe:
            continue
        dedupe.add(normalized)
        urls.append(normalized)
        if len(urls) >= max(settings.web_ingest_max_images, 1):
            break
    return urls


def _web_text_chunks(text: str) -> list[dict[str, Any]]:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []

    max_chars = max(settings.web_ingest_max_chars, 5000)
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars]

    chunks: list[dict[str, Any]] = []
    size = max(settings.ingest_chunk_size_chars, 200)
    overlap = max(min(settings.ingest_chunk_overlap_chars, size - 50), 0)
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(
                {
                    "chunk_type": "text",
                    "text": piece,
                    "chunk_meta": {"source": "web_text"},
                }
            )
        if end >= len(normalized):
            break
        start = end - overlap
    return chunks


def _cap_chunk_entries(
    *,
    text_entries: list[dict[str, Any]],
    table_summaries: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    max_chunks = settings.web_ingest_max_chunks
    if max_chunks <= 0:
        return table_summaries + text_entries + table_rows

    selected = table_summaries[:max_chunks]
    remaining = max(max_chunks - len(selected), 0)
    if remaining == 0:
        return selected

    text_budget = remaining // 2
    row_budget = remaining - text_budget
    if text_budget == 0 and text_entries:
        text_budget = 1
        row_budget = max(remaining - 1, 0)
    if row_budget == 0 and table_rows and remaining > 1:
        row_budget = 1
        text_budget = max(remaining - 1, 0)

    selected.extend(text_entries[:text_budget])
    selected.extend(table_rows[:row_budget])
    return selected[:max_chunks]


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


def _format_to_mime(fmt: str | None) -> tuple[str, str]:
    normalized = (fmt or "").lower()
    if normalized == "jpeg":
        return "image/jpeg", "jpg"
    if normalized in {"jpg", "png", "webp", "gif", "bmp", "tiff"}:
        if normalized == "jpg":
            normalized = "jpeg"
        return f"image/{normalized}", "jpg" if normalized == "jpeg" else normalized
    return "application/octet-stream", "bin"


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
    if (longer / shorter) > settings.image_max_aspect_ratio:
        return False
    return True


def _download_images(image_urls: list[str]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for index, image_url in enumerate(image_urls, start=1):
        try:
            payload, content_type = _fetch_url_bytes(
                image_url,
                accept="image/*",
                max_bytes=8_000_000,
            )
        except WebIngestionError:
            continue

        mime_type = content_type.split(";")[0].strip() or "application/octet-stream"

        width = 0
        height = 0
        image_format: str | None = None
        try:
            with Image.open(BytesIO(payload)) as img:
                width, height = img.size
                image_format = img.format
        except (UnidentifiedImageError, OSError):
            continue

        normalized_mime, extension = _format_to_mime(image_format)
        if normalized_mime != "application/octet-stream":
            mime_type = normalized_mime
        elif not mime_type.startswith("image/"):
            continue

        images.append(
            {
                "image_index": index,
                "source_url": image_url,
                "bytes": payload,
                "mime_type": mime_type,
                "file_bytes": len(payload),
                "width": width,
                "height": height,
                "extension": extension,
            }
        )

    return images


def _caption_images(images: list[dict[str, Any]]) -> list[str]:
    if not images:
        return []
    if settings.vision_provider.lower().strip() != "openai":
        raise WebIngestionError("Only VISION_PROVIDER=openai is implemented for webpage ingestion.")

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
            raise WebIngestionError(str(exc)) from exc
        captions.append(caption)
    return captions


def _ensure_docs_set(
    conn,
    *,
    user_id: str,
    docs_set_id: int | None,
    docs_set_name: str | None,
    root_url: str,
) -> int:
    if docs_set_id is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM docs_sets WHERE id = %s;", (docs_set_id,))
            row = cur.fetchone()
        if not row:
            raise WebIngestionError("docs_set_id does not exist.")
        return int(row["id"])

    parsed_root = urlparse(root_url)
    default_name = parsed_root.hostname or "Web docs set"
    selected_name = (docs_set_name or "").strip() or default_name

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO docs_sets (name, source_type, root_url, created_by)
            VALUES (%s, 'web', %s, %s)
            RETURNING id;
            """,
            (selected_name[:255], root_url, user_id),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def _find_existing_web_document(conn, *, docs_set_id: int, normalized_url: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_name, source_url, status, text_chunk_count, image_count, source_storage_key
            FROM documents
            WHERE source_type = 'web' AND docs_set_id = %s AND source_url_normalized = %s
            ORDER BY id DESC
            LIMIT 1;
            """,
            (docs_set_id, normalized_url),
        )
        row = cur.fetchone()
    return row


def _mark_discovered_link(
    conn,
    *,
    link_id: int,
    status: str,
    ingested_document_id: int | None = None,
    last_error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE web_discovered_links
            SET
              status = %s,
              ingested_document_id = %s,
              last_error = %s,
              updated_at = NOW()
            WHERE id = %s;
            """,
            (status, ingested_document_id, (last_error or None), link_id),
        )
    conn.commit()


def _insert_discovered_links(
    conn,
    *,
    source_document_id: int,
    docs_set_id: int,
    links: list[dict[str, Any]],
    current_page_normalized_url: str,
) -> None:
    with conn.cursor() as cur:
        for link in links:
            normalized_url = link["normalized_url"]
            if normalized_url == current_page_normalized_url:
                continue

            existing = _find_existing_web_document(conn, docs_set_id=docs_set_id, normalized_url=normalized_url)
            status = "ingested" if existing else "discovered"
            ingested_document_id = int(existing["id"]) if existing else None

            cur.execute(
                """
                INSERT INTO web_discovered_links (
                  source_document_id,
                  docs_set_id,
                  url,
                  normalized_url,
                  link_text,
                  same_domain,
                  status,
                  ingested_document_id,
                  updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (source_document_id, normalized_url)
                DO UPDATE SET
                  url = EXCLUDED.url,
                  link_text = EXCLUDED.link_text,
                  same_domain = EXCLUDED.same_domain,
                  status = EXCLUDED.status,
                  ingested_document_id = EXCLUDED.ingested_document_id,
                  updated_at = NOW();
                """,
                (
                    source_document_id,
                    docs_set_id,
                    link["url"],
                    normalized_url,
                    link.get("link_text"),
                    bool(link["same_domain"]),
                    status,
                    ingested_document_id,
                ),
            )
    conn.commit()


def ingest_webpage_document(
    conn,
    *,
    user_id: str,
    source_url: str,
    docs_set_id: int | None = None,
    docs_set_name: str | None = None,
    parent_document_id: int | None = None,
    from_discovered_link_id: int | None = None,
) -> dict[str, Any]:
    normalized_url = normalize_url(source_url)
    docs_set_id = _ensure_docs_set(
        conn,
        user_id=user_id,
        docs_set_id=docs_set_id,
        docs_set_name=docs_set_name,
        root_url=normalized_url,
    )

    existing = _find_existing_web_document(conn, docs_set_id=docs_set_id, normalized_url=normalized_url)
    if existing:
        if from_discovered_link_id is not None:
            _mark_discovered_link(
                conn,
                link_id=from_discovered_link_id,
                status="ingested",
                ingested_document_id=int(existing["id"]),
            )
        return {
            "document_id": int(existing["id"]),
            "docs_set_id": docs_set_id,
            "source_name": str(existing["source_name"]),
            "source_url": str(existing["source_url"]),
            "status": str(existing["status"]),
            "text_chunk_count": int(existing["text_chunk_count"] or 0),
            "image_count": int(existing["image_count"] or 0),
            "source_storage_key": str(existing.get("source_storage_key") or ""),
            "reused_existing": True,
        }

    payload, content_type = _fetch_url_bytes(
        normalized_url,
        accept="text/html,application/xhtml+xml,text/plain",
        max_bytes=max(settings.web_ingest_max_chars, 5000) * 6,
    )
    decoded = payload.decode(_extract_charset(content_type), errors="ignore")

    parsed = urlparse(normalized_url)
    page_title = parsed.hostname or "Web page"
    text_content = ""
    discovered_links: list[dict[str, Any]] = []
    image_urls: list[str] = []
    table_summaries: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []

    if "text/plain" in content_type:
        text_content = " ".join(decoded.split()).strip()
        snapshot_bytes = decoded.encode("utf-8", errors="ignore")
        snapshot_content_type = "text/plain; charset=utf-8"
        snapshot_extension = "txt"
    else:
        soup = BeautifulSoup(decoded, "html.parser")
        if soup.title and soup.title.get_text(" ", strip=True):
            page_title = soup.title.get_text(" ", strip=True)[:255]

        discovered_links = _discover_links(soup, normalized_url)
        image_urls = _discover_image_urls(soup, normalized_url)
        table_summaries, table_rows = _table_chunk_entries(soup)

        text_soup = BeautifulSoup(decoded, "html.parser")
        for tag in text_soup(["script", "style", "noscript"]):
            tag.decompose()
        for table in text_soup.find_all("table"):
            table.decompose()
        text_content = " ".join(text_soup.stripped_strings).strip()

        snapshot_bytes = decoded.encode("utf-8", errors="ignore")
        snapshot_content_type = "text/html; charset=utf-8"
        snapshot_extension = "html"

    if not text_content and not table_rows and not table_summaries:
        raise WebIngestionError("No usable text or table content found on webpage.")

    text_entries = _web_text_chunks(text_content)
    chunk_entries = _cap_chunk_entries(
        text_entries=text_entries,
        table_summaries=table_summaries,
        table_rows=table_rows,
    )
    if not chunk_entries:
        raise WebIngestionError("Webpage is too short after normalization.")

    vectors = _embed_texts_for_ingest([entry["text"] for entry in chunk_entries])

    document_id: int | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (
              source_type,
              source_name,
              source_url,
              source_url_normalized,
              source_parent_document_id,
              docs_set_id,
              created_by,
              status
            )
            VALUES ('web', %s, %s, %s, %s, %s, %s, 'processing')
            RETURNING id;
            """,
            (
                page_title,
                normalized_url,
                normalized_url,
                parent_document_id,
                docs_set_id,
                user_id,
            ),
        )
        row = cur.fetchone()
        document_id = int(row["id"])
    conn.commit()

    snapshot_key = f"documents/{document_id}/web/source.{snapshot_extension}"

    try:
        ensure_bucket(settings.s3_bucket_documents)
        ensure_bucket(settings.s3_bucket_assets)
        upload_bytes(
            bucket_name=settings.s3_bucket_documents,
            key=snapshot_key,
            data=snapshot_bytes,
            content_type=snapshot_content_type,
        )

        with conn.cursor() as cur:
            for entry, vector in zip(chunk_entries, vectors):
                cur.execute(
                    """
                    INSERT INTO text_chunks (document_id, page_start, page_end, chunk_type, chunk_meta, text, embedding)
                    VALUES (%s, NULL, NULL, %s, %s::jsonb, %s, %s::vector);
                    """,
                    (
                        document_id,
                        entry["chunk_type"],
                        json.dumps(entry.get("chunk_meta") or {}, ensure_ascii=True),
                        entry["text"],
                        embedding_to_vector_literal(vector),
                    ),
                )

        downloaded_images = _download_images(image_urls)
        image_rows_with_ids: list[dict[str, Any]] = []

        with conn.cursor() as cur:
            for image in downloaded_images:
                image_key = f"documents/{document_id}/web/images/{image['image_index']}.{image['extension']}"
                upload_bytes(
                    bucket_name=settings.s3_bucket_assets,
                    key=image_key,
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
                    VALUES (%s, NULL, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        document_id,
                        image["image_index"],
                        image_key,
                        image["mime_type"],
                        image["file_bytes"],
                        image["width"],
                        image["height"],
                    ),
                )
                image_id = int(cur.fetchone()["id"])
                image_rows_with_ids.append({**image, "image_id": image_id, "storage_key": image_key})

        eligible_for_caption = [item for item in image_rows_with_ids if _passes_vision_policy(item)]
        eligible_for_caption.sort(key=lambda item: int(item["width"]) * int(item["height"]), reverse=True)
        if settings.ingest_max_vision_images > 0:
            eligible_for_caption = eligible_for_caption[: settings.ingest_max_vision_images]

        captions = _caption_images(eligible_for_caption) if eligible_for_caption else []
        caption_vectors = _embed_texts_for_ingest(captions) if captions else []

        with conn.cursor() as cur:
            for image, caption, vector in zip(eligible_for_caption, captions, caption_vectors):
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
                (snapshot_key, len(chunk_entries), len(downloaded_images), document_id),
            )
        conn.commit()

        if discovered_links:
            _insert_discovered_links(
                conn,
                source_document_id=document_id,
                docs_set_id=docs_set_id,
                links=discovered_links,
                current_page_normalized_url=normalized_url,
            )

        if from_discovered_link_id is not None:
            _mark_discovered_link(
                conn,
                link_id=from_discovered_link_id,
                status="ingested",
                ingested_document_id=document_id,
            )

        return {
            "document_id": document_id,
            "docs_set_id": docs_set_id,
            "source_name": page_title,
            "source_url": normalized_url,
            "status": "ready",
            "text_chunk_count": len(chunk_entries),
            "image_count": len(downloaded_images),
            "source_storage_key": snapshot_key,
            "reused_existing": False,
        }
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("UPDATE documents SET status = 'failed' WHERE id = %s;", (document_id,))
        conn.commit()
        if from_discovered_link_id is not None:
            _mark_discovered_link(
                conn,
                link_id=from_discovered_link_id,
                status="failed",
                last_error=str(exc)[:1000],
            )
        if isinstance(exc, WebIngestionError):
            raise
        raise WebIngestionError(str(exc)) from exc


def ingest_linked_pages_batch(
    conn,
    *,
    user_id: str,
    source_document_id: int,
    max_pages: int,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, docs_set_id, source_type
            FROM documents
            WHERE id = %s;
            """,
            (source_document_id,),
        )
        source = cur.fetchone()
    if not source:
        raise WebIngestionError("Source document for linked-page ingest does not exist.")
    if source["source_type"] != "web":
        raise WebIngestionError("Linked-page ingest is only supported for web source documents.")
    docs_set_id = source.get("docs_set_id")
    if docs_set_id is None:
        raise WebIngestionError("Source web document is missing docs_set_id.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, normalized_url
            FROM web_discovered_links
            WHERE source_document_id = %s
              AND status = 'discovered'
              AND same_domain = TRUE
            ORDER BY id ASC
            LIMIT %s;
            """,
            (source_document_id, max_pages),
        )
        links = cur.fetchall()

    attempted = len(links)
    ingested = 0
    skipped = 0
    failed = 0
    ingested_document_ids: list[int] = []

    for link in links:
        link_id = int(link["id"])
        link_url = str(link["normalized_url"])
        try:
            result = ingest_webpage_document(
                conn,
                user_id=user_id,
                source_url=link_url,
                docs_set_id=int(docs_set_id),
                parent_document_id=source_document_id,
                from_discovered_link_id=link_id,
            )
            if result.get("reused_existing"):
                skipped += 1
            else:
                ingested += 1
                ingested_document_ids.append(int(result["document_id"]))
        except Exception as exc:
            failed += 1
            _mark_discovered_link(
                conn,
                link_id=link_id,
                status="failed",
                last_error=str(exc)[:1000],
            )

    return {
        "source_document_id": source_document_id,
        "attempted": attempted,
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "ingested_document_ids": ingested_document_ids,
    }

import json
import re
from uuid import uuid4
from typing import Any

from psycopg.types.json import Jsonb

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import (
    OpenAIClientError,
    embed_texts,
    generate_image_bytes,
    generate_text_response,
)
from app.storage import ensure_bucket, generate_presigned_get_url, upload_bytes

OFF_TOPIC_TERMS = {
    "weather",
    "nba",
    "nfl",
    "football score",
    "movie review",
    "recipe",
    "horoscope",
    "lottery",
}

STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "build",
    "building",
    "by",
    "can",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "tell",
    "the",
    "their",
    "those",
    "to",
    "what",
    "with",
    "words",
    "you",
    "your",
}

NAV_NOISE_PHRASES = {
    "skip to content",
    "main navigation",
    "sidebar navigation",
    "appearance menu",
    "return to top",
}


class AnswerProviderError(Exception):
    """Raised when answer provider invocation fails."""


def ensure_user(conn, email: str, full_name: str | None) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, full_name, role, last_login)
            VALUES (%s, %s, 'user', NOW())
            ON CONFLICT (email)
            DO UPDATE SET full_name = EXCLUDED.full_name, last_login = NOW()
            RETURNING id::text;
            """,
            (email, full_name),
        )
        row = cur.fetchone()
    return row["id"]


def tokenize(question: str, broaden: bool) -> list[str]:
    min_len = 3 if broaden else 4
    raw = [t for t in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(t) >= min_len]

    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if token in STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)

    max_tokens = 12 if broaden else 10
    return tokens[:max_tokens]


def _row_dedupe_key(row: dict[str, Any]) -> str:
    source_name = str(row.get("source_name") or "").strip().lower()
    text_head = " ".join(str(row.get("chunk_text", "")).split())[:180]

    chunk_id = row.get("chunk_id")
    if chunk_id is not None and text_head:
        return f"text:{source_name}:{text_head}"
    if chunk_id is not None:
        return f"chunk:{int(chunk_id)}"

    image_id = row.get("image_id")
    if image_id is not None:
        return f"image:{int(image_id)}"

    return f"fallback:{row.get('document_id')}:{text_head}"


def _row_source_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("source_type") or "").strip().lower(),
        str(row.get("source_name") or "").strip().lower(),
        str(row.get("source_url") or "").strip().lower(),
    )


def _merge_retrieval_rows(
    embedding_rows: list[dict[str, Any]],
    keyword_rows: list[dict[str, Any]],
    *,
    limit: int,
    broaden: bool,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    # Keep semantic ranking first, then fill lexical matches, while avoiding over-concentration
    # on one duplicate source (for example multiple re-ingests of the same PDF).
    combined = list(embedding_rows) + list(keyword_rows)
    source_cap = 2 if broaden else 1

    selected: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    source_counts: dict[tuple[str, str, str], int] = {}

    def is_nav_noise(row: dict[str, Any]) -> bool:
        text = " ".join(str(row.get("chunk_text", "")).split()).lower()
        if not text:
            return False
        matches = sum(1 for phrase in NAV_NOISE_PHRASES if phrase in text)
        return matches >= 2

    for row in combined:
        if is_nav_noise(row):
            continue

        row_key = _row_dedupe_key(row)
        if row_key in seen_keys:
            continue

        source_key = _row_source_key(row)
        source_count = source_counts.get(source_key, 0)
        if source_count >= source_cap:
            continue

        selected.append(row)
        seen_keys.add(row_key)
        source_counts[source_key] = source_count + 1
        if len(selected) >= limit:
            return selected

    # Second pass: if still below limit, relax source cap but keep dedupe.
    # Prefer lexical matches first to capture concrete lists/labels.
    supplemental = list(keyword_rows) + list(embedding_rows)
    for row in supplemental:
        if is_nav_noise(row):
            continue
        row_key = _row_dedupe_key(row)
        if row_key in seen_keys:
            continue
        selected.append(row)
        seen_keys.add(row_key)
        if len(selected) >= limit:
            break

    return selected


def retrieve_chunks(conn, question: str, broaden: bool = False) -> list[dict[str, Any]]:
    limit = max(settings.ask_top_k + (4 if broaden else 0), 1)
    embedding_rows = _retrieve_chunks_embedding(conn, question, broaden)
    keyword_rows = _retrieve_chunks_keyword(conn, question, broaden)

    if not embedding_rows and not keyword_rows:
        return []
    if embedding_rows and not keyword_rows:
        return embedding_rows[:limit]
    if keyword_rows and not embedding_rows:
        return keyword_rows[:limit]

    merged = _merge_retrieval_rows(embedding_rows, keyword_rows, limit=limit, broaden=broaden)
    merged.sort(key=lambda row: float(row.get("similarity") or -1.0), reverse=True)
    return merged[:limit]


def _retrieve_chunks_embedding(conn, question: str, broaden: bool) -> list[dict[str, Any]]:
    if settings.embeddings_provider.lower().strip() != "openai":
        return []

    try:
        vectors = embed_texts([question], model=settings.embeddings_model)
    except OpenAIClientError:
        return []
    if not vectors:
        return []

    query_vector = embedding_to_vector_literal(vectors[0])
    limit = max(settings.ask_top_k + (4 if broaden else 0), 1)
    candidate_limit = min(limit * 3, 60)

    text_sql = """
        SELECT
          t.id AS chunk_id,
          t.text AS chunk_text,
          t.chunk_type AS chunk_type,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          NULL::bigint AS image_id,
          NULL::text AS image_storage_key,
          NULL::integer AS page_number,
          t.chunk_type AS evidence_type,
          (1 - (t.embedding <=> %s::vector)) AS similarity
        FROM text_chunks t
        JOIN documents d ON d.id = t.document_id
        WHERE t.embedding IS NOT NULL
          AND d.status = 'ready'
        ORDER BY t.embedding <=> %s::vector
        LIMIT %s;
    """
    image_sql = """
        SELECT
          NULL::bigint AS chunk_id,
          ic.caption_text AS chunk_text,
          'image'::text AS chunk_type,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          di.id AS image_id,
          di.storage_key AS image_storage_key,
          di.page_number,
          'image'::text AS evidence_type,
          (1 - (ic.embedding <=> %s::vector)) AS similarity
        FROM image_captions ic
        JOIN document_images di ON di.id = ic.image_id
        JOIN documents d ON d.id = di.document_id
        WHERE ic.embedding IS NOT NULL
          AND d.status = 'ready'
        ORDER BY ic.embedding <=> %s::vector
        LIMIT %s;
    """

    with conn.cursor() as cur:
        cur.execute(text_sql, (query_vector, query_vector, candidate_limit))
        text_rows = cur.fetchall()
        cur.execute(image_sql, (query_vector, query_vector, candidate_limit))
        image_rows = cur.fetchall()

    combined = text_rows + image_rows
    combined.sort(key=lambda row: float(row.get("similarity") or -1.0), reverse=True)
    return combined[:candidate_limit]


def _retrieve_chunks_keyword(conn, question: str, broaden: bool) -> list[dict[str, Any]]:
    tokens = tokenize(question, broaden)
    if not tokens:
        return []

    text_match = " OR ".join(["t.text ILIKE %s" for _ in tokens])
    image_match = " OR ".join(["ic.caption_text ILIKE %s" for _ in tokens])
    text_score = " + ".join(["CASE WHEN t.text ILIKE %s THEN 1 ELSE 0 END" for _ in tokens])
    image_score = " + ".join(["CASE WHEN ic.caption_text ILIKE %s THEN 1 ELSE 0 END" for _ in tokens])
    if "node" in tokens or "nodes" in tokens:
        text_score = f"({text_score}) + (CASE WHEN t.text ILIKE '%%technical name:%%' THEN 3 ELSE 0 END)"

    text_sql = f"""
        SELECT
          t.id AS chunk_id,
          t.text AS chunk_text,
          t.chunk_type AS chunk_type,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          NULL::bigint AS image_id,
          NULL::text AS image_storage_key,
          NULL::integer AS page_number,
          t.chunk_type AS evidence_type,
          ({text_score})::double precision AS similarity
        FROM text_chunks t
        JOIN documents d ON d.id = t.document_id
        WHERE d.status = 'ready' AND ({text_match})
        ORDER BY similarity DESC, t.id DESC
        LIMIT %s;
    """

    image_sql = f"""
        SELECT
          NULL::bigint AS chunk_id,
          ic.caption_text AS chunk_text,
          'image'::text AS chunk_type,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          di.id AS image_id,
          di.storage_key AS image_storage_key,
          di.page_number,
          'image'::text AS evidence_type,
          ({image_score})::double precision AS similarity
        FROM image_captions ic
        JOIN document_images di ON di.id = ic.image_id
        JOIN documents d ON d.id = di.document_id
        WHERE d.status = 'ready' AND ({image_match})
        ORDER BY similarity DESC, ic.id DESC
        LIMIT %s;
    """

    limit = max(settings.ask_top_k + (4 if broaden else 0), 1)
    candidate_limit = min(limit * 4, 80)
    like_tokens = [f"%{token}%" for token in tokens]
    text_params = like_tokens + like_tokens + [candidate_limit]
    image_params = like_tokens + like_tokens + [candidate_limit]

    with conn.cursor() as cur:
        cur.execute(text_sql, text_params)
        text_rows = cur.fetchall()
        cur.execute(image_sql, image_params)
        image_rows = cur.fetchall()

    return (text_rows + image_rows)[:candidate_limit]


def is_out_of_scope(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in OFF_TOPIC_TERMS)


def _collect_webpage_links(rows: list[dict[str, Any]]) -> list[str]:
    webpage_links: list[str] = []
    for row in rows:
        if row.get("source_type") == "web" and row.get("source_url"):
            link = str(row["source_url"])
            if link not in webpage_links:
                webpage_links.append(link)
    return webpage_links


def _collect_image_urls(rows: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    seen_keys: set[str] = set()
    context_document_ids = {
        int(row["document_id"])
        for row in rows
        if row.get("document_id") is not None and row.get("evidence_type") != "image"
    }

    for row in rows:
        if row.get("evidence_type") != "image":
            continue

        row_document_id = row.get("document_id")
        if context_document_ids and row_document_id is not None and int(row_document_id) not in context_document_ids:
            continue

        storage_key = row.get("image_storage_key")
        if not storage_key:
            continue
        storage_key = str(storage_key)
        if storage_key in seen_keys:
            continue
        seen_keys.add(storage_key)
        try:
            url = generate_presigned_get_url(
                bucket_name=settings.s3_bucket_assets,
                key=storage_key,
                expires_seconds=3600,
            )
        except Exception:
            continue
        urls.append(url)
        if len(urls) >= 3:
            break
    return urls


def _confidence_for_mode(fallback_mode: str) -> int:
    if fallback_mode == "none":
        return 82
    if fallback_mode == "broadened_retrieval":
        return 72
    if fallback_mode == "model_knowledge":
        return 42
    return 18


def _context_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No indexed context was retrieved."

    lines: list[str] = []
    for row in rows[:10]:
        normalized = " ".join(str(row.get("chunk_text", "")).split())
        source_name = row.get("source_name") or "unknown-source"
        source_type = row.get("source_type") or "unknown"
        source_url = row.get("source_url") or ""
        evidence_type = row.get("evidence_type") or "text"
        chunk_type = row.get("chunk_type") or "text"
        page = row.get("page_number")
        page_part = f" page={page}" if page is not None else ""
        lines.append(
            f"- source={source_name} type={source_type} evidence={evidence_type} chunk_type={chunk_type}{page_part} url={source_url}\n"
            f"  chunk={normalized[:500]}"
        )
    return "\n".join(lines)


def _grounding_mode() -> str:
    mode = settings.answer_grounding_mode.strip().lower()
    if mode in {"strict", "balanced", "expansive"}:
        return mode
    return "balanced"


def _system_prompt_for_mode(mode: str) -> str:
    _ = mode
    return (
        "You are ContextForge, an enterprise technical assistant.\n\n"
        "Primary behavior:\n"
        "- Answer in clear, practical, operator-ready language.\n"
        "- Do not mention retrieval, indexing, chunks, grounding, or sources unless explicitly asked.\n"
        "- Do not give generic filler.\n\n"
        "For procedural questions (install/setup/configure/migrate/upgrade/troubleshoot):\n"
        "- Return a step-by-step runbook.\n"
        "- Prefer concrete commands, file paths, config keys, and verification checks from provided context.\n"
        "- Keep commands exact when available.\n"
        "- Include: prerequisites, execution steps, verification, and rollback/safety notes (if relevant).\n"
        "- If critical data is missing, state exactly what is missing and ask one focused follow-up question.\n\n"
        "Knowledge use:\n"
        "- Use provided context as primary truth.\n"
        "- You may add general domain knowledge only when it helps complete the procedure.\n"
        "- Enrich with relevant external knowledge when it clearly improves answer quality and does not conflict with provided context.\n"
        "- Clearly separate inferred recommendations from explicit documented steps.\n\n"
        "Self-review:\n"
        "- Before finalizing, silently review your draft and improve specificity, actionability, and correctness.\n"
        "- Remove vague statements when concrete guidance is available.\n\n"
        "Output style:\n"
        "- Plain English only.\n"
        "- Concise, structured, and actionable.\n"
        "- No markdown headings unless user asks."
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}


def _question_requests_visual(question: str) -> bool:
    q = question.lower()
    visual_terms = (
        "diagram",
        "draw",
        "image",
        "visual",
        "flowchart",
        "architecture",
        "show me",
        "chart",
        "illustrat",
    )
    return any(term in q for term in visual_terms)


def _maybe_generate_answer_image(question: str, answer: str, rows: list[dict[str, Any]]) -> list[str]:
    if not settings.generated_images_enabled:
        return []
    if settings.answer_provider.strip().lower() != "openai":
        return []

    context_text = _context_rows(rows)
    decision_prompt = (
        "Decide if an explanatory image should be generated for this answer.\n"
        "Return JSON only with keys:\n"
        "- generate (boolean)\n"
        "- prompt (string, empty when generate=false)\n\n"
        "Generate image when the user explicitly asks for visual output OR when a workflow/architecture/process visual would materially help.\n"
        "Question:\n"
        f"{question}\n\n"
        "Answer draft:\n"
        f"{answer}\n\n"
        "Indexed context summary:\n"
        f"{context_text}\n"
    )
    try:
        decision_raw = generate_text_response(
            model=settings.answer_model,
            system_prompt="Return strict JSON only. No markdown.",
            user_prompt=decision_prompt,
            max_output_tokens=220,
        )
    except OpenAIClientError:
        return []

    decision = _extract_json_object(decision_raw)
    should_generate = bool(decision.get("generate", False))
    image_prompt = str(decision.get("prompt", "")).strip()
    if not should_generate:
        if not _question_requests_visual(question):
            return []
        image_prompt = (
            "Create a clean explanatory enterprise diagram. "
            f"User request: {question}\n"
            f"Answer summary: {answer[:700]}"
        )
    elif not image_prompt:
        image_prompt = (
            "Create a clean explanatory enterprise diagram based on this request: "
            f"{question}"
        )

    max_images = max(settings.generated_image_max_per_answer, 0)
    if max_images <= 0:
        return []

    generated_urls: list[str] = []
    ensure_bucket(settings.s3_bucket_assets)

    for _ in range(max_images):
        try:
            image_bytes = generate_image_bytes(
                model=settings.generated_image_model,
                prompt=image_prompt,
                size=settings.generated_image_size,
                quality=settings.generated_image_quality,
            )
        except OpenAIClientError:
            break

        storage_key = f"generated/{uuid4()}.png"
        try:
            upload_bytes(
                bucket_name=settings.s3_bucket_assets,
                key=storage_key,
                data=image_bytes,
                content_type="image/png",
            )
            url = generate_presigned_get_url(
                bucket_name=settings.s3_bucket_assets,
                key=storage_key,
                expires_seconds=3600,
            )
        except Exception:
            break
        generated_urls.append(url)

    return generated_urls


def _generate_answer_openai(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> str:
    grounded = "yes" if rows else "no"
    context_text = _context_rows(rows)
    mode = _grounding_mode()
    system_prompt = _system_prompt_for_mode(mode)
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Fallback mode: {fallback_mode}\n"
        f"Indexed context available: {grounded}\n\n"
        f"Indexed context:\n{context_text}\n\n"
        "Answer requirements:\n"
        "1. Provide a direct answer.\n"
        "2. Prefer short paragraphs and short bullet lists.\n"
        "3. If context is absent, provide best-effort domain guidance and say it is based on general knowledge.\n"
        "4. If context is present, you may still add useful general domain knowledge when relevant.\n"
        "5. Do not include source citations or retrieval commentary."
    )
    try:
        return generate_text_response(
            model=settings.answer_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=700,
        )
    except OpenAIClientError as exc:
        raise AnswerProviderError(str(exc)) from exc


def _generate_answer_ollama_placeholder(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> str:
    if rows:
        return (
            "Ollama provider is configured, but provider execution is not implemented yet. "
            "Indexed context was retrieved and persisted, but answer generation is currently disabled for Ollama. "
            f"Question: {question}"
        )
    return (
        "Ollama provider is configured, but provider execution is not implemented yet. "
        "No indexed context answer was generated. "
        f"Fallback mode: {fallback_mode}. Question: {question}"
    )


def build_answer(
    question: str, rows: list[dict[str, Any]], fallback_mode: str
) -> tuple[str, int, bool, list[str], list[str], list[str]]:
    webpage_links = _collect_webpage_links(rows)
    image_urls = _collect_image_urls(rows)

    if fallback_mode == "out_of_scope":
        return (
            "I could not find relevant indexed sources, and this request appears outside the scope of "
            "ContextForge (company knowledge and related domain topics).",
            _confidence_for_mode(fallback_mode),
            False,
            [],
            [],
            [],
        )

    provider = settings.answer_provider.lower().strip()
    if provider == "openai":
        answer = _generate_answer_openai(question, rows, fallback_mode)
    elif provider == "ollama":
        answer = _generate_answer_ollama_placeholder(question, rows, fallback_mode)
    else:
        raise AnswerProviderError(f"Unsupported ANSWER_PROVIDER value: {settings.answer_provider}")

    grounded = bool(rows)
    confidence = _confidence_for_mode(fallback_mode)
    generated_image_urls = _maybe_generate_answer_image(question, answer, rows)
    return answer, confidence, grounded, webpage_links, image_urls, generated_image_urls


def persist_ask_history(
    conn,
    *,
    user_id: str,
    user_email: str,
    question: str,
    answer: str,
    confidence_percent: int,
    grounded: bool,
    fallback_mode: str,
    retrieval_outcome: str,
    rows: list[dict[str, Any]],
    conversation_id: str | None,
) -> None:
    documents_used: list[dict[str, Any]] = []
    chunks_used: list[int] = []
    images_used: list[int] = []
    webpage_links: list[str] = []

    seen_document_ids = set()
    for row in rows:
        chunk_id = row.get("chunk_id")
        if chunk_id is not None:
            chunks_used.append(int(chunk_id))

        image_id = row.get("image_id")
        if image_id is not None:
            image_int = int(image_id)
            if image_int not in images_used:
                images_used.append(image_int)

        document_id = int(row["document_id"])
        if document_id not in seen_document_ids:
            seen_document_ids.add(document_id)
            documents_used.append(
                {
                    "document_id": document_id,
                    "source_name": row.get("source_name"),
                    "source_type": row.get("source_type"),
                }
            )

        if row.get("source_type") == "web" and row.get("source_url"):
            url = str(row["source_url"])
            if url not in webpage_links:
                webpage_links.append(url)

    evidence = {
        "retrieved_chunk_count": len(chunks_used),
        "retrieved_image_count": len(images_used),
        "retrieval_outcome": retrieval_outcome,
        "fallback_mode": fallback_mode,
        "answer_provider": settings.answer_provider,
        "answer_model": settings.answer_model,
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ask_history (
              user_id,
              user_email,
              question,
              answer,
              conversation_id,
              documents_used,
              chunks_used,
              images_used,
              webpage_links,
              confidence_percent,
              grounded,
              retrieval_outcome,
              fallback_mode,
              evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                user_id,
                user_email,
                question,
                answer,
                conversation_id,
                Jsonb(documents_used),
                Jsonb(chunks_used),
                Jsonb(images_used),
                Jsonb(webpage_links),
                confidence_percent,
                grounded,
                retrieval_outcome,
                fallback_mode,
                Jsonb(evidence),
            ),
        )

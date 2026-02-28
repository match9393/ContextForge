import re
from typing import Any

from psycopg.types.json import Jsonb

from app.config import settings
from app.db import embedding_to_vector_literal
from app.openai_client import OpenAIClientError, embed_texts, generate_text_response

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
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(t) >= min_len]
    return tokens[:6]


def retrieve_chunks(conn, question: str, broaden: bool = False) -> list[dict[str, Any]]:
    rows = _retrieve_chunks_embedding(conn, question, broaden)
    if rows:
        return rows
    return _retrieve_chunks_keyword(conn, question, broaden)


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

    sql = """
        SELECT
          t.id AS chunk_id,
          t.text AS chunk_text,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          (1 - (t.embedding <=> %s::vector)) AS similarity
        FROM text_chunks t
        JOIN documents d ON d.id = t.document_id
        WHERE t.embedding IS NOT NULL
        ORDER BY t.embedding <=> %s::vector
        LIMIT %s;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (query_vector, query_vector, limit))
        rows = cur.fetchall()
    return rows


def _retrieve_chunks_keyword(conn, question: str, broaden: bool) -> list[dict[str, Any]]:
    tokens = tokenize(question, broaden)
    if not tokens:
        return []

    conditions = " OR ".join(["t.text ILIKE %s" for _ in tokens])
    sql = f"""
        SELECT
          t.id AS chunk_id,
          t.text AS chunk_text,
          d.id AS document_id,
          d.source_name,
          d.source_type,
          d.source_url,
          NULL::double precision AS similarity
        FROM text_chunks t
        JOIN documents d ON d.id = t.document_id
        WHERE {conditions}
        ORDER BY t.id DESC
        LIMIT %s;
    """
    params = [f"%{token}%" for token in tokens]
    params.append(max(settings.ask_top_k + (4 if broaden else 0), 1))

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return rows


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
    for row in rows[:4]:
        normalized = " ".join(str(row.get("chunk_text", "")).split())
        source_name = row.get("source_name") or "unknown-source"
        source_type = row.get("source_type") or "unknown"
        source_url = row.get("source_url") or ""
        lines.append(
            f"- source={source_name} type={source_type} url={source_url}\n"
            f"  chunk={normalized[:500]}"
        )
    return "\n".join(lines)


def _generate_answer_openai(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> str:
    grounded = "yes" if rows else "no"
    context_text = _context_rows(rows)
    system_prompt = (
        "You are ContextForge, an enterprise knowledge assistant. "
        "Write concise, practical, synthesized answers in your own words. "
        "Do not output citation blocks. "
        "If no indexed context is provided, explicitly say that you are answering from model knowledge."
    )
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Fallback mode: {fallback_mode}\n"
        f"Indexed context available: {grounded}\n\n"
        f"Indexed context:\n{context_text}\n\n"
        "Answer requirements:\n"
        "1. Provide a direct answer.\n"
        "2. Mention whether indexed context was found.\n"
        "3. If context is absent, provide best-effort domain guidance.\n"
        "4. No citation formatting."
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


def build_answer(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> tuple[str, int, bool, list[str]]:
    webpage_links = _collect_webpage_links(rows)

    if fallback_mode == "out_of_scope":
        return (
            "I could not find relevant indexed sources, and this request appears outside the scope of "
            "ContextForge (company knowledge and related domain topics).",
            _confidence_for_mode(fallback_mode),
            False,
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
    return answer, confidence, grounded, webpage_links


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
    webpage_links: list[str] = []

    seen_document_ids = set()
    for row in rows:
        chunk_id = int(row["chunk_id"])
        chunks_used.append(chunk_id)

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s, %s, %s, %s, %s, %s);
            """,
            (
                user_id,
                user_email,
                question,
                answer,
                conversation_id,
                Jsonb(documents_used),
                Jsonb(chunks_used),
                Jsonb(webpage_links),
                confidence_percent,
                grounded,
                retrieval_outcome,
                fallback_mode,
                Jsonb(evidence),
            ),
        )

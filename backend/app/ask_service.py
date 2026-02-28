import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from psycopg.types.json import Jsonb

from app.config import settings

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
          d.source_url
        FROM text_chunks t
        JOIN documents d ON d.id = t.document_id
        WHERE {conditions}
        ORDER BY t.id DESC
        LIMIT %s;
    """
    params = [f"%{token}%" for token in tokens]
    params.append(6 if broaden else 3)

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


def _extract_openai_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text", "")).strip()
    if direct:
        return direct

    collected: list[str] = []
    for item in payload.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                collected.append(str(part["text"]))
    return "\n".join(collected).strip()


def _generate_answer_openai(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> str:
    if not settings.openai_api_key:
        raise AnswerProviderError("OPENAI_API_KEY is required when ANSWER_PROVIDER=openai")

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

    request_payload = {
        "model": settings.answer_model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "max_output_tokens": 700,
    }
    request_data = json.dumps(request_payload).encode("utf-8")

    request = Request(
        url="https://api.openai.com/v1/responses",
        data=request_data,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=settings.openai_timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise AnswerProviderError(f"OpenAI request failed ({exc.code}): {details[:300]}") from exc
    except URLError as exc:
        raise AnswerProviderError(f"OpenAI network error: {exc.reason}") from exc

    payload = json.loads(body)
    output_text = _extract_openai_text(payload)
    if not output_text:
        raise AnswerProviderError("OpenAI returned an empty answer")

    return output_text


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

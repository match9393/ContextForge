import re
from typing import Any

from psycopg.types.json import Jsonb

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


def build_answer(question: str, rows: list[dict[str, Any]], fallback_mode: str) -> tuple[str, int, bool, list[str]]:
    webpage_links: list[str] = []

    for row in rows:
        if row.get("source_type") == "web" and row.get("source_url"):
            link = str(row["source_url"])
            if link not in webpage_links:
                webpage_links.append(link)

    if rows:
        snippets = []
        for row in rows[:2]:
            normalized = " ".join(str(row["chunk_text"]).split())
            snippets.append(normalized[:220])

        joined = " ".join(snippets)
        answer = (
            "Based on indexed sources, here is a synthesized answer: "
            f"{joined}"
        )

        confidence = 82 if fallback_mode == "none" else 72
        grounded = True
        return answer, confidence, grounded, webpage_links

    if fallback_mode == "out_of_scope":
        return (
            "I could not find relevant indexed sources, and this request appears outside the scope of "
            "ContextForge (company knowledge and related domain topics).",
            18,
            False,
            [],
        )

    return (
        "I could not find supporting indexed sources for this question. I am providing a best-effort "
        "domain answer using model knowledge only."
        f" Question: {question}",
        42,
        False,
        [],
    )


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

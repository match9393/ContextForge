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

QUESTION_TYPES = {"procedural", "conceptual", "comparison", "troubleshooting", "other"}
PROCEDURAL_HINT_TERMS = {
    "install",
    "installation",
    "setup",
    "configure",
    "configuration",
    "migrate",
    "migration",
    "upgrade",
    "deploy",
    "deployment",
    "troubleshoot",
    "fix",
}
COMMAND_PATTERN = re.compile(
    r"(^|[\s`])("
    r"dnf|yum|apt|apk|docker|docker compose|systemctl|psql|curl|chmod|chown|mkdir|cd|"
    r"createuser|createdb|repmgr|pg_basebackup|openssl|logrotate"
    r")([\s`]|$)",
    re.IGNORECASE,
)
PATH_PATTERN = re.compile(r"(^|\s)/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")
CONFIG_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b\s*=")


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


def _is_navigation_noise_text(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return False
    matches = sum(1 for phrase in NAV_NOISE_PHRASES if phrase in normalized)
    return matches >= 2


def _infer_question_type(question: str) -> str:
    q = question.lower()
    if any(term in q for term in PROCEDURAL_HINT_TERMS):
        return "procedural"
    if "difference" in q or "compare" in q:
        return "comparison"
    if "why" in q or "what is" in q:
        return "conceptual"
    if "error" in q or "fail" in q or "issue" in q:
        return "troubleshooting"
    return "other"


def _sanitize_query_variants(candidates: list[Any], *, fallback: str, limit: int) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if len(value) < 3:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(value)
        if len(unique) >= max(limit, 1):
            break

    if fallback.lower() not in seen:
        unique.insert(0, fallback)
    return unique[: max(limit, 1)]


def _filter_off_topic_query_variants(question: str, queries: list[str]) -> list[str]:
    q = question.lower()
    blocked_terms = {
        "pip",
        "conda",
        "poetry",
        "venv",
        "npm",
        "yarn",
        "maven",
        "gradle",
        "python",
        "node.js",
        "java",
    }

    filtered: list[str] = []
    for query in queries:
        lower = query.lower()
        if any(term in lower for term in blocked_terms) and not any(term in q for term in blocked_terms):
            continue
        filtered.append(query)

    if not filtered:
        return [question]
    return filtered


def _heuristic_query_variants(question: str, question_type: str, evidence_needs: list[str]) -> list[str]:
    variants = [question]
    if question_type == "procedural":
        variants.append(f"{question} step by step")
        variants.append(f"{question} commands config paths verification")
    elif question_type == "troubleshooting":
        variants.append(f"{question} troubleshooting checks")
    elif question_type == "comparison":
        variants.append(f"{question} differences tradeoffs")
    else:
        variants.append(f"{question} key concepts")

    if evidence_needs:
        need_fragment = " ".join(evidence_needs[:4])
        variants.append(f"{question} {need_fragment}")

    return _sanitize_query_variants(
        variants,
        fallback=question,
        limit=max(settings.retrieval_query_variants_max, 1),
    )


def plan_retrieval(question: str) -> dict[str, Any]:
    fallback_type = _infer_question_type(question)
    fallback_needs = ["core_steps"] if fallback_type == "procedural" else ["key_points"]
    fallback_queries = _heuristic_query_variants(question, fallback_type, fallback_needs)

    if not settings.retrieval_planner_enabled or settings.answer_provider.lower().strip() != "openai":
        return {
            "question_type": fallback_type,
            "evidence_needs": fallback_needs,
            "query_variants": fallback_queries,
            "source": "heuristic",
        }

    planner_prompt = (
        "Return JSON only with keys:\n"
        "- question_type: one of procedural|conceptual|comparison|troubleshooting|other\n"
        "- evidence_needs: short list of evidence required to answer well\n"
        "- query_variants: 2-4 concrete retrieval queries\n\n"
        "Rules:\n"
        "- Prefer factual, operator-focused retrieval intents.\n"
        "- For procedural questions, include queries for commands, paths, configuration keys, prerequisites, and verification.\n"
        "- Keep domain assumptions conservative: do not invent language/runtime package managers unless the question explicitly asks for them.\n"
        "- Stay close to the product terms in the question.\n"
        "- Keep each query concise and specific.\n\n"
        f"Question:\n{question}"
    )

    try:
        raw = generate_text_response(
            model=settings.answer_model,
            system_prompt="You are a retrieval planner. Output strict JSON only.",
            user_prompt=planner_prompt,
            max_output_tokens=260,
        )
    except OpenAIClientError:
        return {
            "question_type": fallback_type,
            "evidence_needs": fallback_needs,
            "query_variants": fallback_queries,
            "source": "heuristic_fallback",
        }

    parsed = _extract_json_object(raw)
    question_type = str(parsed.get("question_type", fallback_type)).strip().lower()
    if question_type not in QUESTION_TYPES:
        question_type = fallback_type

    evidence_raw = parsed.get("evidence_needs")
    evidence_needs: list[str]
    if isinstance(evidence_raw, list):
        evidence_needs = [str(item).strip() for item in evidence_raw if str(item).strip()][:8]
    else:
        evidence_needs = fallback_needs
    if not evidence_needs:
        evidence_needs = fallback_needs

    query_raw = parsed.get("query_variants")
    query_candidates = query_raw if isinstance(query_raw, list) else fallback_queries
    query_variants = _sanitize_query_variants(
        _filter_off_topic_query_variants(question, list(query_candidates)),
        fallback=question,
        limit=max(settings.retrieval_query_variants_max, 1),
    )

    return {
        "question_type": question_type,
        "evidence_needs": evidence_needs,
        "query_variants": query_variants,
        "source": "planner_llm",
    }


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

    for row in combined:
        if _is_navigation_noise_text(str(row.get("chunk_text", ""))):
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
        if _is_navigation_noise_text(str(row.get("chunk_text", ""))):
            continue
        row_key = _row_dedupe_key(row)
        if row_key in seen_keys:
            continue
        selected.append(row)
        seen_keys.add(row_key)
        if len(selected) >= limit:
            break

    return selected


def _row_has_command_signal(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get("chunk_text", "")).split())
    if not text:
        return False
    return bool(COMMAND_PATTERN.search(text))


def _row_has_path_signal(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get("chunk_text", "")).split())
    if not text:
        return False
    return bool(PATH_PATTERN.search(text))


def _row_has_config_signal(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get("chunk_text", "")).split())
    if not text:
        return False
    return bool(CONFIG_PATTERN.search(text))


def _rerank_rows(
    rows: list[dict[str, Any]],
    *,
    question: str,
    question_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    scored: list[dict[str, Any]] = []
    question_text = question.lower()
    question_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", question_text)
        if len(token) >= 4 and token not in STOPWORDS
    }
    explicit_apio_core = "apio core" in question_text

    for row in rows:
        base = float(row.get("similarity") or 0.0)
        score = base
        source_text = " ".join(
            [
                str(row.get("source_name") or "").lower(),
                str(row.get("source_url") or "").lower(),
            ]
        )
        source_tokens = {token for token in re.findall(r"[a-z0-9]+", source_text) if len(token) >= 4}
        overlap = len(question_tokens & source_tokens)
        score += min(overlap * 0.25, 1.5)
        if explicit_apio_core:
            if "apio core" in source_text or "apiocore" in source_text:
                score += 1.5
            else:
                score -= 0.6

        if question_type == "procedural":
            if _row_has_command_signal(row):
                score += 3.0
            if _row_has_path_signal(row):
                score += 1.6
            if _row_has_config_signal(row):
                score += 1.4

            chunk_text = str(row.get("chunk_text", "")).lower()
            if "prerequisite" in chunk_text or "requirements" in chunk_text:
                score += 0.7
            if "verify" in chunk_text or "status" in chunk_text or "health" in chunk_text:
                score += 0.7

        row_copy = dict(row)
        row_copy["retrieval_score"] = score
        scored.append(row_copy)

    scored.sort(
        key=lambda row: (
            float(row.get("retrieval_score") or 0.0),
            float(row.get("similarity") or 0.0),
        ),
        reverse=True,
    )

    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    source_counts: dict[tuple[str, str, str], int] = {}
    source_cap = 2 if question_type == "procedural" else 1
    for row in scored:
        key = _row_dedupe_key(row)
        if key in seen_keys:
            continue
        source_key = _row_source_key(row)
        count = source_counts.get(source_key, 0)
        if count >= source_cap:
            continue
        seen_keys.add(key)
        source_counts[source_key] = count + 1
        deduped.append(row)
        if len(deduped) >= limit:
            return deduped

    for row in scored:
        key = _row_dedupe_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break

    return deduped


def _should_second_pass(
    *,
    question: str,
    question_type: str,
    evidence_needs: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not settings.retrieval_second_pass_enabled:
        return {"needs_second_pass": False, "query_variants": [], "reason": "disabled"}
    if max(settings.retrieval_max_rounds, 1) < 2:
        return {"needs_second_pass": False, "query_variants": [], "reason": "single_round_config"}

    command_hits = sum(1 for row in rows if _row_has_command_signal(row))
    config_hits = sum(1 for row in rows if _row_has_config_signal(row))
    row_count = len(rows)

    heuristic_needs_more = False
    if question_type == "procedural":
        heuristic_needs_more = row_count < max(settings.ask_top_k // 2, 3) or (command_hits + config_hits) < 2
    elif row_count == 0:
        heuristic_needs_more = True

    fallback_queries = _sanitize_query_variants(
        _filter_off_topic_query_variants(
            question,
            [
                question,
                f"{question} exact commands",
                f"{question} config keys file paths",
                f"{question} verification steps",
                f"{question} {' '.join(evidence_needs[:3])}",
            ],
        ),
        fallback=question,
        limit=max(settings.retrieval_second_pass_query_variants_max, 1),
    )

    if settings.answer_provider.lower().strip() != "openai":
        return {
            "needs_second_pass": heuristic_needs_more,
            "query_variants": fallback_queries if heuristic_needs_more else [],
            "reason": "heuristic_non_openai",
        }

    gap_prompt = (
        "Return JSON only with keys:\n"
        "- needs_second_pass (boolean)\n"
        "- reason (short string)\n"
        "- query_variants (up to 3 focused retrieval queries)\n\n"
        "Decide if current retrieved evidence is enough to answer precisely.\n"
        "For procedural questions, require concrete commands/config/path/verification evidence.\n\n"
        "Do not suggest language/runtime package-manager queries unless explicitly present in the user question.\n\n"
        f"Question:\n{question}\n\n"
        f"Question type: {question_type}\n"
        f"Evidence needs: {', '.join(evidence_needs)}\n\n"
        "Current context summary:\n"
        f"{_context_rows(rows, max_rows=12)}"
    )

    try:
        raw = generate_text_response(
            model=settings.answer_model,
            system_prompt="You are a retrieval gap checker. Output strict JSON only.",
            user_prompt=gap_prompt,
            max_output_tokens=220,
        )
        parsed = _extract_json_object(raw)
        llm_needs = bool(parsed.get("needs_second_pass", False))
        reason = str(parsed.get("reason") or "").strip() or "llm_decision"
        query_raw = parsed.get("query_variants")
        query_candidates = list(query_raw) if isinstance(query_raw, list) else fallback_queries
        queries = _sanitize_query_variants(
            _filter_off_topic_query_variants(question, query_candidates),
            fallback=question,
            limit=max(settings.retrieval_second_pass_query_variants_max, 1),
        )
        needs = llm_needs or heuristic_needs_more
        return {
            "needs_second_pass": needs,
            "query_variants": queries if needs else [],
            "reason": reason if needs else "sufficient",
        }
    except OpenAIClientError:
        return {
            "needs_second_pass": heuristic_needs_more,
            "query_variants": fallback_queries if heuristic_needs_more else [],
            "reason": "heuristic_fallback",
        }


def retrieve_chunks(
    conn,
    question: str,
    *,
    broaden: bool = False,
    query_variants: list[str] | None = None,
    question_type: str = "other",
) -> list[dict[str, Any]]:
    limit = max(settings.ask_top_k + (4 if broaden else 0), 1)
    all_queries = _sanitize_query_variants(
        [question] + (query_variants or []),
        fallback=question,
        limit=max(settings.retrieval_query_variants_max, 1),
    )

    embedding_rows: list[dict[str, Any]] = []
    keyword_rows: list[dict[str, Any]] = []
    for query in all_queries:
        embedding_rows.extend(_retrieve_chunks_embedding(conn, query, broaden))
        keyword_rows.extend(_retrieve_chunks_keyword(conn, query, broaden))

    if not embedding_rows and not keyword_rows:
        return []

    merged = _merge_retrieval_rows(embedding_rows, keyword_rows, limit=limit * 2, broaden=broaden)
    return _rerank_rows(merged, question=question, question_type=question_type, limit=limit)


def retrieve_chunks_with_planner(conn, question: str, *, broaden: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = plan_retrieval(question)
    question_type = str(plan.get("question_type") or "other")
    evidence_needs = [str(item) for item in plan.get("evidence_needs", []) if str(item).strip()]
    plan_queries = [str(item) for item in plan.get("query_variants", []) if str(item).strip()]

    first_rows = retrieve_chunks(
        conn,
        question,
        broaden=broaden,
        query_variants=plan_queries,
        question_type=question_type,
    )

    retrieval_trace: dict[str, Any] = {
        "question_type": question_type,
        "evidence_needs": evidence_needs,
        "planner_source": str(plan.get("source") or "unknown"),
        "rounds": [
            {
                "round": 1,
                "broaden": broaden,
                "queries": _sanitize_query_variants(
                    [question] + plan_queries,
                    fallback=question,
                    limit=max(settings.retrieval_query_variants_max, 1),
                ),
                "result_count": len(first_rows),
                "top_chunk_ids": [int(row["chunk_id"]) for row in first_rows if row.get("chunk_id") is not None][:8],
            }
        ],
    }

    if max(settings.retrieval_max_rounds, 1) < 2:
        return first_rows, retrieval_trace

    second_pass = _should_second_pass(
        question=question,
        question_type=question_type,
        evidence_needs=evidence_needs,
        rows=first_rows,
    )
    retrieval_trace["second_pass"] = {
        "needed": bool(second_pass.get("needs_second_pass")),
        "reason": str(second_pass.get("reason") or ""),
    }
    if not second_pass.get("needs_second_pass"):
        return first_rows, retrieval_trace

    second_queries = [str(item) for item in second_pass.get("query_variants", []) if str(item).strip()]
    second_rows = retrieve_chunks(
        conn,
        question,
        broaden=True,
        query_variants=second_queries,
        question_type=question_type,
    )
    combined = _rerank_rows(
        first_rows + second_rows,
        question=question,
        question_type=question_type,
        limit=max(settings.ask_top_k + (4 if broaden else 0), 1),
    )

    retrieval_trace["rounds"].append(
        {
            "round": 2,
            "broaden": True,
            "queries": _sanitize_query_variants(
                [question] + second_queries,
                fallback=question,
                limit=max(settings.retrieval_second_pass_query_variants_max, 1),
            ),
            "result_count": len(second_rows),
            "top_chunk_ids": [int(row["chunk_id"]) for row in second_rows if row.get("chunk_id") is not None][:8],
        }
    )

    return combined, retrieval_trace


def _extract_question_urls(question: str) -> set[str]:
    urls = set(re.findall(r"https?://[^\s)>\"]+", question, flags=re.IGNORECASE))
    return {url.strip().rstrip(".,;:").lower() for url in urls if url.strip()}


def _doc_full_text_for_answer(conn, *, document_id: int, max_chars: int) -> tuple[str, int, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_type, text
            FROM text_chunks
            WHERE document_id = %s
            ORDER BY id ASC;
            """,
            (document_id,),
        )
        chunk_rows = cur.fetchall()

    parts: list[str] = []
    for row in chunk_rows:
        chunk_text = " ".join(str(row.get("text") or "").split()).strip()
        if not chunk_text:
            continue
        if _is_navigation_noise_text(chunk_text):
            continue
        chunk_type = str(row.get("chunk_type") or "text")
        if chunk_type == "text":
            parts.append(chunk_text)
        else:
            parts.append(f"[{chunk_type}] {chunk_text}")

    merged = "\n".join(parts).strip()
    if not merged:
        return "", 0, False

    max_allowed = max(max_chars, 4000)
    if len(merged) <= max_allowed:
        return merged, len(parts), False

    head = max_allowed // 2
    tail = max_allowed - head
    truncated = (
        merged[:head]
        + "\n...[document middle truncated for token budget]...\n"
        + merged[-tail:]
    )
    return truncated, len(parts), True


def _select_documents_for_full_context(question: str, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if not rows or limit <= 0:
        return []

    explicit_urls = _extract_question_urls(question)
    doc_best: dict[int, dict[str, Any]] = {}
    for row in rows:
        doc_id_raw = row.get("document_id")
        if doc_id_raw is None:
            continue
        doc_id = int(doc_id_raw)
        score = float(row.get("retrieval_score") or row.get("similarity") or 0.0)
        entry = doc_best.get(doc_id)
        if entry is None or score > float(entry.get("score", -10_000.0)):
            doc_best[doc_id] = {
                "document_id": doc_id,
                "source_name": row.get("source_name"),
                "source_type": row.get("source_type"),
                "source_url": row.get("source_url"),
                "score": score,
            }

    docs = list(doc_best.values())
    for doc in docs:
        source_url = str(doc.get("source_url") or "").strip().lower()
        doc["direct_url_match"] = 1 if source_url and source_url in explicit_urls else 0

    docs.sort(
        key=lambda item: (
            int(item.get("direct_url_match") or 0),
            float(item.get("score") or 0.0),
        ),
        reverse=True,
    )
    return docs[:limit]


def build_answer_context_rows(
    conn,
    *,
    question: str,
    rows: list[dict[str, Any]],
    use_full_doc_context: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not rows:
        return rows, {"enabled": False, "selected_documents": []}
    if not use_full_doc_context or not settings.retrieval_full_doc_context_enabled:
        return rows, {"enabled": False, "selected_documents": []}

    selected_docs = _select_documents_for_full_context(
        question,
        rows,
        limit=max(settings.retrieval_full_doc_context_top_docs, 1),
    )
    if not selected_docs:
        return rows, {"enabled": False, "selected_documents": []}

    synthetic_rows: list[dict[str, Any]] = []
    trace_docs: list[dict[str, Any]] = []
    for doc in selected_docs:
        document_id = int(doc["document_id"])
        full_text, full_chunk_count, truncated = _doc_full_text_for_answer(
            conn,
            document_id=document_id,
            max_chars=settings.retrieval_full_doc_context_max_chars_per_doc,
        )
        if not full_text:
            continue

        synthetic_rows.append(
            {
                "chunk_id": None,
                "chunk_text": full_text,
                "chunk_type": "document_full",
                "document_id": document_id,
                "source_name": doc.get("source_name"),
                "source_type": doc.get("source_type"),
                "source_url": doc.get("source_url"),
                "image_id": None,
                "image_storage_key": None,
                "page_number": None,
                "evidence_type": "document_full",
                "similarity": float(doc.get("score") or 0.0),
                "retrieval_score": float(doc.get("score") or 0.0),
            }
        )
        trace_docs.append(
            {
                "document_id": document_id,
                "source_name": doc.get("source_name"),
                "source_url": doc.get("source_url"),
                "full_text_chunk_count": full_chunk_count,
                "full_text_chars": len(full_text),
                "truncated": truncated,
            }
        )

    if not synthetic_rows:
        return rows, {"enabled": False, "selected_documents": []}

    return synthetic_rows + rows, {"enabled": True, "selected_documents": trace_docs}


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


def _context_rows(rows: list[dict[str, Any]], *, max_rows: int | None = None) -> str:
    if not rows:
        return "No indexed context was retrieved."

    row_cap = max_rows if max_rows is not None else max(settings.retrieval_context_rows_for_answer, 1)
    lines: list[str] = []
    for row in rows[: max(row_cap, 1)]:
        normalized = " ".join(str(row.get("chunk_text", "")).split())
        source_name = row.get("source_name") or "unknown-source"
        source_type = row.get("source_type") or "unknown"
        source_url = row.get("source_url") or ""
        evidence_type = row.get("evidence_type") or "text"
        chunk_type = row.get("chunk_type") or "text"
        page = row.get("page_number")
        page_part = f" page={page}" if page is not None else ""
        chunk_limit = 500
        if evidence_type == "document_full" or chunk_type == "document_full":
            chunk_limit = max(settings.retrieval_full_doc_context_max_chars_per_doc, 4000)
        lines.append(
            f"- source={source_name} type={source_type} evidence={evidence_type} chunk_type={chunk_type}{page_part} url={source_url}\n"
            f"  chunk={normalized[:chunk_limit]}"
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


def _prefer_landscape_image(question: str) -> bool:
    q = question.lower()
    landscape_terms = (
        "diagram",
        "architecture",
        "flow",
        "flowchart",
        "pipeline",
        "workflow",
        "topology",
    )
    return any(term in q for term in landscape_terms)


def _maybe_generate_answer_image(question: str, answer: str, rows: list[dict[str, Any]]) -> list[str]:
    if not settings.generated_images_enabled:
        return []
    if settings.answer_provider.strip().lower() != "openai":
        return []
    # Avoid surprising low-quality auto-illustrations: generate only on explicit visual intent.
    if not _question_requests_visual(question):
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

    style_guardrails = (
        "Output requirements for generated diagram:\n"
        "- Keep ALL content fully inside the canvas with safe margins.\n"
        "- Do not crop any boxes, arrows, or text.\n"
        "- Use short labels and correct spelling.\n"
        "- Prefer clear, high-contrast, minimal style.\n"
        "- Avoid dense tiny text.\n"
    )
    image_prompt = f"{style_guardrails}\n\n{image_prompt}"

    max_images = max(settings.generated_image_max_per_answer, 0)
    if max_images <= 0:
        return []

    generated_urls: list[str] = []
    ensure_bucket(settings.s3_bucket_assets)

    selected_size = settings.generated_image_size
    if _prefer_landscape_image(question) and selected_size == "1024x1024":
        selected_size = "1536x1024"

    selected_quality = settings.generated_image_quality
    if _question_requests_visual(question) and selected_quality == "medium":
        selected_quality = "high"

    for _ in range(max_images):
        try:
            image_bytes = generate_image_bytes(
                model=settings.generated_image_model,
                prompt=image_prompt,
                size=selected_size,
                quality=selected_quality,
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
    retrieval_trace: dict[str, Any] | None = None,
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
        "retrieval_trace": retrieval_trace or {},
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

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings


class OpenAIClientError(Exception):
    """Raised when OpenAI API calls fail."""


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise OpenAIClientError("OPENAI_API_KEY is required for OpenAI provider operations")

    request_data = json.dumps(payload).encode("utf-8")
    request = Request(
        url=url,
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
        raise OpenAIClientError(f"OpenAI request failed ({exc.code}): {details[:300]}") from exc
    except URLError as exc:
        raise OpenAIClientError(f"OpenAI network error: {exc.reason}") from exc

    return json.loads(body)


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    if not texts:
        return []

    selected_model = model or settings.embeddings_model
    payload = {
        "model": selected_model,
        "input": texts,
    }
    response = _post_json("https://api.openai.com/v1/embeddings", payload)
    data = response.get("data", [])
    if len(data) != len(texts):
        raise OpenAIClientError(
            f"OpenAI embeddings response size mismatch: expected {len(texts)} got {len(data)}"
        )

    vectors: list[list[float]] = []
    for item in data:
        vectors.append(item.get("embedding", []))
    return vectors


def _extract_response_output_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text", "")).strip()
    if direct:
        return direct

    collected: list[str] = []
    for item in payload.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                collected.append(str(part["text"]))
    return "\n".join(collected).strip()


def generate_text_response(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 700,
) -> str:
    payload = {
        "model": model,
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
        "max_output_tokens": max_output_tokens,
    }
    response = _post_json("https://api.openai.com/v1/responses", payload)
    output_text = _extract_response_output_text(response)
    if not output_text:
        raise OpenAIClientError("OpenAI returned an empty answer")
    return output_text


def generate_image_caption(
    *,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    max_chars: int,
) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Describe this document image for enterprise retrieval. "
                            "Include key entities, metrics, labels, and what the image shows. "
                            f"Maximum {max_chars} characters."
                        ),
                    },
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        "max_output_tokens": 500,
    }
    response = _post_json("https://api.openai.com/v1/responses", payload)
    output_text = _extract_response_output_text(response)
    if not output_text:
        raise OpenAIClientError("OpenAI returned an empty vision caption")
    return output_text[:max_chars]


def generate_image_bytes(
    *,
    model: str,
    prompt: str,
    size: str = "1024x1024",
    quality: str = "medium",
) -> bytes:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
    }
    response = _post_json("https://api.openai.com/v1/images/generations", payload)
    data = response.get("data", [])
    if not data:
        raise OpenAIClientError("OpenAI image generation returned no data")

    b64 = data[0].get("b64_json")
    if not b64:
        raise OpenAIClientError("OpenAI image generation returned no image payload")
    try:
        return base64.b64decode(b64)
    except Exception as exc:
        raise OpenAIClientError("Failed to decode generated image payload") from exc

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AskResponse(BaseModel):
    answer: str
    confidence_percent: int = Field(ge=0, le=100)
    grounded: bool
    fallback_mode: Literal["none", "broadened_retrieval", "model_knowledge", "out_of_scope"]
    webpage_links: list[str]
    image_urls: list[str] = Field(default_factory=list)


class IngestPdfResponse(BaseModel):
    document_id: int
    source_name: str
    status: str
    page_count: int
    text_chunk_count: int
    image_count: int
    storage_key: str


class AdminDocumentItem(BaseModel):
    id: int
    source_type: str
    source_name: str
    source_url: str | None = None
    status: str
    text_chunk_count: int
    image_count: int
    created_at: datetime
    created_by_email: str | None = None


class AdminDocumentsResponse(BaseModel):
    documents: list[AdminDocumentItem]


class AdminDeleteDocumentResponse(BaseModel):
    document_id: int
    status: Literal["deleted"]


class AdminAskHistoryItem(BaseModel):
    id: int
    created_at: datetime
    user_email: str
    question: str
    fallback_mode: str
    retrieval_outcome: str
    confidence_percent: int = Field(ge=0, le=100)
    grounded: bool
    documents_used: list[dict[str, Any]] = Field(default_factory=list)
    chunks_used: list[int] = Field(default_factory=list)
    images_used: list[int] = Field(default_factory=list)
    webpage_links: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class AdminAskHistoryResponse(BaseModel):
    history: list[AdminAskHistoryItem]

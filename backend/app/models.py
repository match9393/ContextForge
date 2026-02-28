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


class IngestWebRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    docs_set_id: int | None = None
    docs_set_name: str | None = Field(default=None, max_length=255)
    parent_document_id: int | None = None
    discovered_link_id: int | None = None


class IngestWebResponse(BaseModel):
    document_id: int
    docs_set_id: int
    source_name: str
    source_url: str
    status: str
    text_chunk_count: int
    image_count: int
    source_storage_key: str


class AdminDocumentItem(BaseModel):
    id: int
    source_type: str
    source_name: str
    source_url: str | None = None
    source_storage_key: str | None = None
    source_parent_document_id: int | None = None
    docs_set_id: int | None = None
    docs_set_name: str | None = None
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


class AdminDocsSetItem(BaseModel):
    id: int
    name: str
    root_url: str | None = None
    source_type: str
    created_at: datetime
    created_by_email: str | None = None
    document_count: int


class AdminDocsSetsResponse(BaseModel):
    docs_sets: list[AdminDocsSetItem]


class AdminDiscoveredLinkItem(BaseModel):
    id: int
    source_document_id: int
    docs_set_id: int | None = None
    url: str
    normalized_url: str
    link_text: str | None = None
    same_domain: bool
    status: str
    ingested_document_id: int | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminDiscoveredLinksResponse(BaseModel):
    links: list[AdminDiscoveredLinkItem]


class IngestLinkedPagesRequest(BaseModel):
    source_document_id: int
    max_pages: int = Field(default=20, ge=1, le=100)


class IngestLinkedPagesResponse(BaseModel):
    source_document_id: int
    attempted: int
    ingested: int
    skipped: int
    failed: int
    ingested_document_ids: list[int] = Field(default_factory=list)

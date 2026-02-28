from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AskResponse(BaseModel):
    answer: str
    confidence_percent: int = Field(ge=0, le=100)
    grounded: bool
    fallback_mode: Literal["none", "broadened_retrieval", "model_knowledge", "out_of_scope"]
    webpage_links: list[str]

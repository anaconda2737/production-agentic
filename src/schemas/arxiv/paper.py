from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class JudgementBase(BaseModel):
    """Schema for response data."""

    doc_id: str = Field(..., description="Unique document ID (e.g. DLHC010051062022)")
    court: str = Field(..., description="Name of the court")
    case_title: str = Field(..., description="Full case title")
    neutral_citation: Optional[str] = Field(None, description="Neutral citation")
    case_number: Optional[str] = Field(None, description="case number")
    decision_date: Optional[datetime] = Field(None, description="Date of the decision")
    year: Optional[int] = Field(None, description="Year of the judgement")

    petitioners: Optional[List[str]] = Field(None, description="List of petitioners")
    respondents: Optional[List[str]] = Field(None, description="List of respondents")
    judges: Optional[List[str]] = Field(None, description="List of judges")
    author_judge: Optional[str] = Field(None, description="Judge who authored the judgment")

    pdf_path: Optional[str] = Field(None, description="Path to PDF in object storage")
    pdf_url: Optional[str] = Field(None, description="URL to PDF")
    full_text_path: Optional[str] = Field(None, description="Path to extracted full text")

class JudgmentResponse(JudgmentBase):
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JudgmentSearchResponse(BaseModel):
    judgements: List[JudgmentResponse]
    total: int
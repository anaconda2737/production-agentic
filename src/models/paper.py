import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from src.db.interfaces.postgresql import Base
from sqlalchemy.dialects.postgresql import JSONB


class Paper(Base):
    __tablename__ = "judgements"

    # Core judgement metadata
    doc_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #Basic metadata
    court = Column(String,nullable=False, index=True)
    case_title = Column(String, nullable=False)
    neutral_citation = Column(String, index=True)
    case_number = Column(String,index=True)
    decision_date = Column(DateTime,nullable=False)
    #Parties
    petitioners = Column(ARRAY(String), nullable=False)
    respondents = Column(ARRAY(String), nullable=False)
    #judges
    judges = Column(JSON, nullable=False)
    #citations
    citations = Column(JSONB,nullable=True)
    #File Path
    pdf_path = Column(String,nullable=False)
    full_text_path = Column(String,nullable=False)
    pdf_url = Column(String,nullable=True)

   

    # Parsed PDF content (added for comprehensive storage)
    raw_text = Column(Text, nullable=True)
    sections = Column(JSON, nullable=True)
    references = Column(JSON, nullable=True)

    # PDF processing metadata
    parser_used = Column(String, nullable=True)
    parser_metadata = Column(JSON, nullable=True)
    pdf_processed = Column(Boolean, default=False, nullable=False)
    pdf_processing_date = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

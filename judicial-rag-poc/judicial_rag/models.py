from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from judicial_rag.db import Base


def new_id() -> str:
    return str(uuid4())


class Judgment(Base):
    __tablename__ = "judgments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    case_name: Mapped[str] = mapped_column(String(1000), index=True)
    case_number: Mapped[str | None] = mapped_column(String(250), nullable=True, index=True)
    court: Mapped[str | None] = mapped_column(String(250), nullable=True, index=True)
    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    neutral_citation: Mapped[str | None] = mapped_column(String(250), nullable=True, index=True)
    source_uri: Mapped[str] = mapped_column(Text)
    source_document_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingestion_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class JudgmentAlias(Base):
    __tablename__ = "judgment_aliases"
    __table_args__ = (UniqueConstraint("judgment_id", "kind", "normalized_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    judgment_id: Mapped[str] = mapped_column(ForeignKey("judgments.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    value: Mapped[str] = mapped_column(String(1000))
    normalized_value: Mapped[str] = mapped_column(String(1000), index=True)


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(500), unique=True, index=True)


class PartyAlias(Base):
    __tablename__ = "party_aliases"
    __table_args__ = (UniqueConstraint("party_id", "normalized_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(500))
    normalized_value: Mapped[str] = mapped_column(String(500), index=True)


class JudgmentParty(Base):
    __tablename__ = "judgment_parties"
    __table_args__ = (Index("ix_judgment_parties_party_judgment", "party_id", "judgment_id"),)

    judgment_id: Mapped[str] = mapped_column(ForeignKey("judgments.id", ondelete="CASCADE"), primary_key=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), primary_key=True, default="party")


class CaseCitationOccurrence(Base):
    __tablename__ = "case_citation_occurrences"
    __table_args__ = (
        Index("ix_citation_target_citing", "cited_judgment_id", "citing_judgment_id"),
        Index("ix_citation_unresolved_key", "normalized_value", "cited_judgment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    citing_judgment_id: Mapped[str] = mapped_column(ForeignKey("judgments.id", ondelete="CASCADE"), index=True)
    cited_judgment_id: Mapped[str | None] = mapped_column(
        ForeignKey("judgments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw_citation: Mapped[str] = mapped_column(String(1000))
    normalized_value: Mapped[str] = mapped_column(String(1000), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class Provision(Base):
    __tablename__ = "provisions"
    __table_args__ = (UniqueConstraint("statute_key", "provision_type", "provision_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    statute_name: Mapped[str] = mapped_column(String(300), index=True)
    statute_key: Mapped[str] = mapped_column(String(300), index=True)
    provision_type: Mapped[str] = mapped_column(String(20), index=True)
    provision_number: Mapped[str] = mapped_column(String(100), index=True)


class ProvisionOccurrence(Base):
    __tablename__ = "provision_occurrences"
    __table_args__ = (Index("ix_provision_occurrence_provision_judgment", "provision_id", "judgment_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    judgment_id: Mapped[str] = mapped_column(ForeignKey("judgments.id", ondelete="CASCADE"), index=True)
    provision_id: Mapped[str] = mapped_column(ForeignKey("provisions.id", ondelete="CASCADE"), index=True)
    raw_reference: Mapped[str] = mapped_column(String(500))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)


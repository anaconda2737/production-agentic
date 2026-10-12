import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from judicial_rag.chunking import make_chunks
from judicial_rag.config import Settings
from judicial_rag.extraction import ExtractedOccurrence, extract_case_citations, extract_provisions, normalize_identifier
from judicial_rag.models import (
    CaseCitationOccurrence,
    Judgment,
    JudgmentAlias,
    JudgmentParty,
    Party,
    PartyAlias,
    Provision,
    ProvisionOccurrence,
)
from judicial_rag.object_store import ObjectStore, file_sha256
from judicial_rag.pdf_parser import JudgmentParagraph, parse_pdf
from judicial_rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _read_sidecar(pdf_path: Path) -> dict[str, Any]:
    sidecar = pdf_path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    with sidecar.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Sidecar must contain a JSON object: {sidecar}")
    return data


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _valid_alias(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _upsert_judgment_alias(session: Session, judgment_id: str, kind: str, value: str) -> None:
    normalized = normalize_identifier(value)
    if not normalized:
        return
    exists = session.scalar(
        select(JudgmentAlias.id).where(
            JudgmentAlias.judgment_id == judgment_id,
            JudgmentAlias.kind == kind,
            JudgmentAlias.normalized_value == normalized,
        )
    )
    if exists is None:
        session.add(JudgmentAlias(judgment_id=judgment_id, kind=kind, value=value.strip(), normalized_value=normalized))


def _party_for_name(session: Session, name: str) -> Party:
    normalized = normalize_identifier(name)
    alias_ids = set(
        session.scalars(select(PartyAlias.party_id).where(PartyAlias.normalized_value == normalized)).all()
    )
    if len(alias_ids) == 1:
        party = session.get(Party, next(iter(alias_ids)))
        if party:
            return party
    party = session.scalar(select(Party).where(Party.canonical_name == name.strip()))
    if party is None:
        party = Party(canonical_name=name.strip())
        session.add(party)
        session.flush()
    if normalized:
        existing_alias = session.scalar(
            select(PartyAlias.id).where(
                PartyAlias.party_id == party.id,
                PartyAlias.normalized_value == normalized,
            )
        )
        if existing_alias is None:
            session.add(PartyAlias(party_id=party.id, value=name.strip(), normalized_value=normalized))
    return party


def _party_items(session: Session, judgment_id: str, raw_parties: Any) -> list[str]:
    names: list[str] = []
    for item in _as_items(raw_parties):
        if isinstance(item, dict):
            name = _valid_alias(item.get("name"))
            role = _valid_alias(item.get("role")) or "party"
            aliases = [_valid_alias(alias) for alias in _as_items(item.get("aliases"))]
        else:
            name = _valid_alias(item)
            role = "party"
            aliases = []
        if not name:
            continue
        party = _party_for_name(session, name)
        session.merge(JudgmentParty(judgment_id=judgment_id, party_id=party.id, role=role.casefold()))
        for alias in aliases:
            if not alias:
                continue
            normalized = normalize_identifier(alias)
            exists = session.scalar(
                select(PartyAlias.id).where(
                    PartyAlias.party_id == party.id,
                    PartyAlias.normalized_value == normalized,
                )
            )
            if normalized and exists is None:
                session.add(PartyAlias(party_id=party.id, value=alias, normalized_value=normalized))
        names.append(party.canonical_name)
    return names


def _citation_alias_map(session: Session, normalized_values: set[str]) -> dict[str, set[str]]:
    if not normalized_values:
        return {}
    rows = session.execute(
        select(JudgmentAlias.normalized_value, JudgmentAlias.judgment_id).where(
            JudgmentAlias.normalized_value.in_(normalized_values)
        )
    ).all()
    by_key: dict[str, set[str]] = {}
    for key, judgment_id in rows:
        by_key.setdefault(key, set()).add(judgment_id)
    return by_key


def _resolve_citation(alias_map: dict[str, set[str]], normalized: str, citing_id: str) -> str | None:
    matches = set(alias_map.get(normalized, set()))
    matches.discard(citing_id)
    return next(iter(matches)) if len(matches) == 1 else None


def _add_citation(
    session: Session, citing_id: str, occurrence: ExtractedOccurrence, alias_map: dict[str, set[str]]
) -> None:
    session.add(
        CaseCitationOccurrence(
            citing_judgment_id=citing_id,
            cited_judgment_id=_resolve_citation(alias_map, occurrence.normalized_value, citing_id),
            raw_citation=occurrence.value,
            normalized_value=occurrence.normalized_value,
            page_number=occurrence.page_number,
            paragraph_number=occurrence.paragraph_number,
            snippet=occurrence.snippet,
            confidence=occurrence.confidence,
        )
    )


def _add_manual_citation(session: Session, citing_id: str, value: Any, alias_map: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        raw = _valid_alias(value.get("citation") or value.get("case_name") or value.get("case_number"))
        page, paragraph = value.get("page_number"), value.get("paragraph_number")
        snippet = _valid_alias(value.get("snippet"))
    else:
        raw, page, paragraph, snippet = _valid_alias(value), None, None, None
    if not raw:
        return
    normalized = normalize_identifier(raw)
    session.add(
        CaseCitationOccurrence(
            citing_judgment_id=citing_id,
            cited_judgment_id=_resolve_citation(alias_map, normalized, citing_id),
            raw_citation=raw,
            normalized_value=normalized,
            page_number=int(page) if page is not None else None,
            paragraph_number=int(paragraph) if paragraph is not None else None,
            snippet=snippet,
            confidence=1.0,
        )
    )


def _add_provision_occurrences(session: Session, judgment_id: str, provisions: list) -> list[str]:
    payload_keys: set[str] = set()
    for item in provisions:
        provision = session.scalar(
            select(Provision).where(
                Provision.statute_key == item.statute_key,
                Provision.provision_type == item.provision_type,
                Provision.provision_number == item.provision_number,
            )
        )
        if provision is None:
            provision = Provision(
                statute_name=item.statute_name,
                statute_key=item.statute_key,
                provision_type=item.provision_type,
                provision_number=item.provision_number,
            )
            session.add(provision)
            session.flush()
        session.add(
            ProvisionOccurrence(
                judgment_id=judgment_id,
                provision_id=provision.id,
                raw_reference=item.raw_reference,
                page_number=item.page_number,
                paragraph_number=item.paragraph_number,
                snippet=item.snippet,
            )
        )
        payload_keys.add(f"{item.provision_type}:{item.provision_number.casefold()}:{item.statute_key}")
        payload_keys.add(f"{item.provision_type}:{item.provision_number.casefold()}:*")
    return sorted(payload_keys)


def _resolve_pending_citations(session: Session, normalized_values: set[str]) -> int:
    if not normalized_values:
        return 0
    pending = session.scalars(
        select(CaseCitationOccurrence).where(
            CaseCitationOccurrence.cited_judgment_id.is_(None),
            CaseCitationOccurrence.normalized_value.in_(normalized_values),
        )
    ).all()
    if not pending:
        return 0
    by_key = _citation_alias_map(session, normalized_values)
    resolved = 0
    for item in pending:
        matches = set(by_key.get(item.normalized_value, set())) - {item.citing_judgment_id}
        if len(matches) == 1:
            item.cited_judgment_id = next(iter(matches))
            resolved += 1
    return resolved


def ingest_pdf(
    pdf_path: Path,
    session: Session,
    vector_store: VectorStore,
    object_store: ObjectStore,
    settings: Settings,
) -> dict[str, Any]:
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file() or pdf_path.suffix.casefold() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    source_hash = file_sha256(pdf_path)
    existing = session.scalar(select(Judgment).where(Judgment.source_hash == source_hash))
    if existing and existing.ingestion_status == "indexed":
        return {"judgment_id": existing.id, "case_name": existing.case_name, "status": "skipped_existing"}

    sidecar = _read_sidecar(pdf_path)
    paragraphs = parse_pdf(pdf_path, ocr_scanned=settings.ocr_scanned_pdfs)
    case_name = str(sidecar.get("case_name") or sidecar.get("title") or pdf_path.stem).strip()
    source_uri = object_store.put_file(pdf_path, source_hash)

    if existing:
        vector_store.delete_judgment(existing.id)
        session.execute(delete(CaseCitationOccurrence).where(CaseCitationOccurrence.citing_judgment_id == existing.id))
        session.execute(delete(ProvisionOccurrence).where(ProvisionOccurrence.judgment_id == existing.id))
        session.execute(delete(JudgmentParty).where(JudgmentParty.judgment_id == existing.id))
        session.execute(delete(JudgmentAlias).where(JudgmentAlias.judgment_id == existing.id))
        judgment = existing
    else:
        judgment = Judgment(
            source_hash=source_hash,
            case_name=case_name,
            source_uri=source_uri,
            ingestion_status="pending",
        )
        session.add(judgment)
        session.flush()

    judgment.case_name = case_name
    judgment.case_number = _valid_alias(sidecar.get("case_number"))
    judgment.court = _valid_alias(sidecar.get("court"))
    raw_date = _valid_alias(sidecar.get("decision_date"))
    judgment.decision_date = date.fromisoformat(raw_date[:10]) if raw_date else None
    judgment.neutral_citation = _valid_alias(sidecar.get("neutral_citation"))
    judgment.source_uri = source_uri
    judgment.source_document_url = _valid_alias(sidecar.get("source_uri"))
    judgment.ingestion_status = "pending"

    _upsert_judgment_alias(session, judgment.id, "case_name", case_name)
    if judgment.case_number:
        _upsert_judgment_alias(session, judgment.id, "case_number", judgment.case_number)
    if judgment.neutral_citation:
        _upsert_judgment_alias(session, judgment.id, "neutral_citation", judgment.neutral_citation)
    for citation in _as_items(sidecar.get("reporter_citations")):
        alias = _valid_alias(citation)
        if alias:
            _upsert_judgment_alias(session, judgment.id, "reporter_citation", alias)
    for alias in _as_items(sidecar.get("aliases")):
        alias_text = _valid_alias(alias)
        if alias_text:
            _upsert_judgment_alias(session, judgment.id, "case_name", alias_text)

    party_names = _party_items(session, judgment.id, sidecar.get("parties"))
    extracted_citations = extract_case_citations(paragraphs)
    extracted_provisions = extract_provisions(paragraphs)
    manual_citations = _as_items(sidecar.get("cited_cases"))
    manual_keys = {
        normalize_identifier(str(item.get("citation") or item.get("case_name") or item.get("case_number") or ""))
        if isinstance(item, dict)
        else normalize_identifier(str(item or ""))
        for item in manual_citations
    }
    citation_keys = {item.normalized_value for item in extracted_citations} | {key for key in manual_keys if key}
    citation_alias_map = _citation_alias_map(session, citation_keys)
    for occurrence in extracted_citations:
        _add_citation(session, judgment.id, occurrence, citation_alias_map)
    for occurrence in manual_citations:
        _add_manual_citation(session, judgment.id, occurrence, citation_alias_map)
    provision_keys = _add_provision_occurrences(session, judgment.id, extracted_provisions)
    session.flush()

    chunks = make_chunks(paragraphs, settings.max_chunk_words, settings.chunk_overlap_words)
    provisions_by_location: dict[tuple[int, int], list] = {}
    for occurrence in extracted_provisions:
        provisions_by_location.setdefault((occurrence.page_number, occurrence.paragraph_number), []).append(occurrence)
    vector_chunks = []
    for chunk in chunks:
        local_keys: set[str] = set()
        for location in chunk.paragraph_locations:
            for occurrence in provisions_by_location.get(
                (location["page_number"], location["paragraph_number"]), []
            ):
                local_keys.add(
                    f"{occurrence.provision_type}:{occurrence.provision_number.casefold()}:{occurrence.statute_key}"
                )
                local_keys.add(f"{occurrence.provision_type}:{occurrence.provision_number.casefold()}:*")
        vector_chunks.append(
            {
                "text": chunk.text,
                "chunk_index": chunk.chunk_index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "paragraph_start": chunk.paragraph_start,
                "paragraph_end": chunk.paragraph_end,
                "paragraph_locations": chunk.paragraph_locations,
                "case_name": case_name,
                "case_number": judgment.case_number or "",
                "neutral_citation": judgment.neutral_citation or "",
                "court": judgment.court or "",
                "decision_date": judgment.decision_date.isoformat() if judgment.decision_date else "",
                "decision_year": judgment.decision_date.year if judgment.decision_date else 0,
                "party_names": party_names,
                "provision_keys": sorted(local_keys),
                "source_uri": judgment.source_uri,
            }
        )

    # Persist metadata before writing vectors so a failed vector operation can be retried.
    judgment.ingestion_status = "indexing"
    session.commit()
    try:
        indexed_chunks = vector_store.upsert_chunks(judgment.id, vector_chunks)
    except Exception:
        judgment.ingestion_status = "failed"
        session.commit()
        raise

    judgment.ingestion_status = "indexed"
    judgment_aliases = session.scalars(
        select(JudgmentAlias.normalized_value).where(JudgmentAlias.judgment_id == judgment.id)
    ).all()
    resolved = _resolve_pending_citations(session, set(judgment_aliases))
    session.commit()
    return {
        "judgment_id": judgment.id,
        "case_name": judgment.case_name,
        "status": "indexed",
        "pages_with_text": len({paragraph.page_number for paragraph in paragraphs}),
        "chunks_indexed": indexed_chunks,
        "citation_occurrences_extracted": len(extracted_citations) + len(manual_citations),
        "pending_citations_resolved": resolved,
        "provision_occurrences_extracted": len(extracted_provisions),
    }



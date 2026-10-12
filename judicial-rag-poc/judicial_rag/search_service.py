from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from judicial_rag.extraction import normalize_identifier, parse_provision_query
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
from judicial_rag.vector_store import VectorStore


def _judgment_dict(judgment: Judgment) -> dict[str, Any]:
    return {
        "judgment_id": judgment.id,
        "case_name": judgment.case_name,
        "case_number": judgment.case_number,
        "neutral_citation": judgment.neutral_citation,
        "court": judgment.court,
        "decision_date": judgment.decision_date.isoformat() if judgment.decision_date else None,
        "source_uri": judgment.source_uri,
        "source_document_url": judgment.source_document_url,
    }


def _postgres_alias_candidates(
    session: Session, model, query: str, limit: int = 20
) -> list[tuple[str, str, str, float]] | None:
    similarity = func.similarity(model.normalized_value, query).label("similarity")
    try:
        rows = session.execute(
            select(model.normalized_value, model.value, model.judgment_id if model is JudgmentAlias else model.party_id, similarity)
            .where(model.normalized_value.op("%", is_comparison=True)(query))
            .order_by(desc(similarity))
            .limit(limit)
        ).all()
        return [(row[0], row[1], row[2], float(row[3])) for row in rows]
    except Exception:
        # The local SQLite prototype and PostgreSQL before pg_trgm migration use the fallback below.
        session.rollback()
        return None


def _fuzzy_alias_candidates(
    session: Session, model, query: str, target_column: str, limit: int = 20
) -> list[tuple[str, str, str, float]]:
    rows = session.execute(select(model.normalized_value, model.value, getattr(model, target_column))).all()
    scored = [
        (normalized, value, target, fuzz.ratio(query, normalized) / 100.0)
        for normalized, value, target in rows
    ]
    return sorted((row for row in scored if row[3] >= 0.50), key=lambda row: row[3], reverse=True)[:limit]


def resolve_case(session: Session, query: str, limit: int = 10) -> list[dict[str, Any]]:
    normalized = normalize_identifier(query)
    exact = session.scalars(
        select(JudgmentAlias).where(JudgmentAlias.normalized_value == normalized)
    ).all()
    if exact:
        candidate_ids = {item.judgment_id for item in exact}
        scores = {candidate_id: 1.0 for candidate_id in candidate_ids}
    else:
        if session.bind and session.bind.dialect.name == "postgresql":
            rows = _postgres_alias_candidates(session, JudgmentAlias, normalized, limit)
        else:
            rows = None
        if rows is None:
            rows = _fuzzy_alias_candidates(session, JudgmentAlias, normalized, "judgment_id", limit)
        scores: dict[str, float] = {}
        for _key, _value, candidate_id, score in rows:
            scores[candidate_id] = max(score, scores.get(candidate_id, 0.0))
    if not scores:
        return []
    judgments = session.scalars(select(Judgment).where(Judgment.id.in_(scores))).all()
    return [
        {**_judgment_dict(judgment), "match_score": round(scores[judgment.id], 4)}
        for judgment in sorted(judgments, key=lambda item: scores[item.id], reverse=True)[:limit]
    ]


def resolve_party(session: Session, query: str, limit: int = 10) -> list[dict[str, Any]]:
    normalized = normalize_identifier(query)
    exact = session.scalars(select(PartyAlias).where(PartyAlias.normalized_value == normalized)).all()
    if exact:
        score_by_id = {item.party_id: 1.0 for item in exact}
    else:
        if session.bind and session.bind.dialect.name == "postgresql":
            rows = _postgres_alias_candidates(session, PartyAlias, normalized, limit)
        else:
            rows = None
        if rows is None:
            rows = _fuzzy_alias_candidates(session, PartyAlias, normalized, "party_id", limit)
        score_by_id: dict[str, float] = {}
        for _key, _value, party_id, score in rows:
            score_by_id[party_id] = max(score, score_by_id.get(party_id, 0.0))
    if not score_by_id:
        return []
    parties = session.scalars(select(Party).where(Party.id.in_(score_by_id))).all()
    return [
        {"party_id": party.id, "name": party.canonical_name, "match_score": round(score_by_id[party.id], 4)}
        for party in sorted(parties, key=lambda item: score_by_id[item.id], reverse=True)[:limit]
    ]


def cited_by(session: Session, query: str, limit: int = 5) -> dict[str, Any]:
    candidates = resolve_case(session, query)
    if not candidates:
        return {"query": query, "resolved": None, "candidates": [], "results": []}
    if len(candidates) > 1 and candidates[0]["match_score"] - candidates[1]["match_score"] < 0.04:
        return {"query": query, "resolved": None, "candidates": candidates, "results": []}

    target_id = candidates[0]["judgment_id"]
    counts = session.execute(
        select(
            CaseCitationOccurrence.citing_judgment_id,
            func.count(CaseCitationOccurrence.id).label("citation_count"),
        )
        .where(CaseCitationOccurrence.cited_judgment_id == target_id)
        .group_by(CaseCitationOccurrence.citing_judgment_id)
        .order_by(desc("citation_count"))
        .limit(limit)
    ).all()
    result_ids = [row[0] for row in counts]
    judgments = {item.id: item for item in session.scalars(select(Judgment).where(Judgment.id.in_(result_ids))).all()}
    occurrences = session.scalars(
        select(CaseCitationOccurrence)
        .where(
            CaseCitationOccurrence.cited_judgment_id == target_id,
            CaseCitationOccurrence.citing_judgment_id.in_(result_ids),
        )
        .order_by(CaseCitationOccurrence.citing_judgment_id, CaseCitationOccurrence.page_number)
    ).all()
    grouped: dict[str, list[CaseCitationOccurrence]] = defaultdict(list)
    for item in occurrences:
        grouped[item.citing_judgment_id].append(item)
    results = []
    for citing_id, count in counts:
        judgment = judgments.get(citing_id)
        if not judgment:
            continue
        results.append(
            {
                **_judgment_dict(judgment),
                "citation_occurrence_count": count,
                "citation_locations": [
                    {
                        "citation_text": item.raw_citation,
                        "page_number": item.page_number,
                        "paragraph_number": item.paragraph_number,
                        "snippet": item.snippet,
                        "confidence": item.confidence,
                    }
                    for item in grouped[citing_id]
                ],
            }
        )
    return {"query": query, "resolved": candidates[0], "candidates": candidates, "results": results}


def party_cooccurrence(session: Session, party_a: str, party_b: str, limit: int = 50) -> dict[str, Any]:
    candidates_a = resolve_party(session, party_a)
    candidates_b = resolve_party(session, party_b)
    if not candidates_a or not candidates_b:
        return {
            "party_a": party_a,
            "party_b": party_b,
            "resolved_party_a": candidates_a[:5],
            "resolved_party_b": candidates_b[:5],
            "results": [],
        }
    for candidates in (candidates_a, candidates_b):
        if len(candidates) > 1 and candidates[0]["match_score"] - candidates[1]["match_score"] < 0.05:
            return {
                "party_a": party_a,
                "party_b": party_b,
                "resolved_party_a": candidates_a[:5],
                "resolved_party_b": candidates_b[:5],
                "results": [],
                "resolution_required": True,
            }
    if candidates_a[0]["party_id"] == candidates_b[0]["party_id"]:
        return {
            "party_a": party_a,
            "party_b": party_b,
            "resolved_party_a": candidates_a[:5],
            "resolved_party_b": candidates_b[:5],
            "results": [],
            "error": "Both names resolved to the same party; refine one of the names.",
        }

    party_ids = [candidates_a[0]["party_id"], candidates_b[0]["party_id"]]
    judgment_ids = session.scalars(
        select(Judgment.id)
        .join(JudgmentParty, JudgmentParty.judgment_id == Judgment.id)
        .where(JudgmentParty.party_id.in_(party_ids))
        .group_by(Judgment.id, Judgment.decision_date)
        .having(func.count(func.distinct(JudgmentParty.party_id)) == 2)
        .order_by(desc(Judgment.decision_date))
        .limit(limit)
    ).all()
    judgments = session.scalars(
        select(Judgment).where(Judgment.id.in_(judgment_ids)).order_by(desc(Judgment.decision_date), Judgment.case_name)
    ).all()
    return {
        "party_a": party_a,
        "party_b": party_b,
        "resolved_party_a": candidates_a[0],
        "resolved_party_b": candidates_b[0],
        "results": [_judgment_dict(item) for item in judgments],
    }


def provision_search(session: Session, query: str, limit: int = 50) -> dict[str, Any]:
    provision_type, provision_number, statute_key = parse_provision_query(query)
    statement = select(Provision).where(
        Provision.provision_type == provision_type,
        func.lower(Provision.provision_number) == provision_number.casefold(),
    )
    if statute_key:
        statement = statement.where(Provision.statute_key == statute_key)
    provisions = session.scalars(statement).all()
    provision_ids = [item.id for item in provisions]
    if not provision_ids:
        return {"query": query, "provisions": [], "results": []}

    counts = session.execute(
        select(ProvisionOccurrence.judgment_id, func.count(ProvisionOccurrence.id).label("mention_count"))
        .where(ProvisionOccurrence.provision_id.in_(provision_ids))
        .group_by(ProvisionOccurrence.judgment_id)
        .order_by(desc("mention_count"))
        .limit(limit)
    ).all()
    judgment_ids = [row[0] for row in counts]
    judgments = {item.id: item for item in session.scalars(select(Judgment).where(Judgment.id.in_(judgment_ids))).all()}
    occurrences = session.scalars(
        select(ProvisionOccurrence).where(
            ProvisionOccurrence.judgment_id.in_(judgment_ids),
            ProvisionOccurrence.provision_id.in_(provision_ids),
        )
    ).all()
    grouped: dict[str, list[ProvisionOccurrence]] = defaultdict(list)
    for item in occurrences:
        grouped[item.judgment_id].append(item)
    return {
        "query": query,
        "provisions": [
            {
                "statute_name": item.statute_name,
                "provision_type": item.provision_type,
                "provision_number": item.provision_number,
            }
            for item in provisions
        ],
        "results": [
            {
                **_judgment_dict(judgments[judgment_id]),
                "mention_count": count,
                "mentions": [
                    {
                        "reference": occurrence.raw_reference,
                        "page_number": occurrence.page_number,
                        "paragraph_number": occurrence.paragraph_number,
                        "snippet": occurrence.snippet,
                    }
                    for occurrence in grouped[judgment_id]
                ],
            }
            for judgment_id, count in counts
            if judgment_id in judgments
        ],
    }


def passage_search(
    vector_store: VectorStore,
    query: str,
    limit: int = 8,
    court: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    provision_key: str | None = None,
) -> list[dict[str, Any]]:
    return vector_store.search(query, limit, court, year_from, year_to, provision_key)


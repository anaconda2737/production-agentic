from contextlib import asynccontextmanager
from typing import AsyncIterator, Generator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from judicial_rag.config import Settings, get_settings
from judicial_rag.db import Database
from judicial_rag.extraction import split_party_query
from judicial_rag.object_store import ObjectStore
from judicial_rag.schemas import (
    AskRequest,
    CaseResolveRequest,
    CitationSearchRequest,
    PartyCooccurrenceRequest,
    PassageSearchRequest,
    ProvisionSearchRequest,
)
from judicial_rag.search_service import cited_by, passage_search, party_cooccurrence, provision_search, resolve_case
from judicial_rag.vector_store import VectorStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database = Database(settings.database_url)
    database.create_tables()
    vector_store = VectorStore(settings)
    app.state.settings = settings
    app.state.database = database
    app.state.vector_store = vector_store
    app.state.object_store = ObjectStore(settings)
    try:
        yield
    finally:
        vector_store.close()
        database.close()


app = FastAPI(
    title="Judicial RAG Prototype",
    description="Local-first search over Indian judgments, case citations, parties, and statutory provisions.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_session(request: Request) -> Generator[Session, None, None]:
    database: Database = request.app.state.database
    with database.session() as session:
        yield session


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Depends(get_session)


@app.get("/healthz")
def health(request: Request, session: Session = SessionDep) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "qdrant": request.app.state.vector_store.health()}


@app.post("/api/v1/cases/resolve")
def case_resolve(body: CaseResolveRequest, session: Session = SessionDep) -> dict:
    return {"query": body.query, "candidates": resolve_case(session, body.query)}


@app.post("/api/v1/citations/cited-by")
def citation_search(body: CitationSearchRequest, session: Session = SessionDep) -> dict:
    return cited_by(session, body.query, body.limit)


@app.post("/api/v1/parties/cooccurrence")
def party_search(body: PartyCooccurrenceRequest, session: Session = SessionDep) -> dict:
    try:
        party_a, party_b = (
            split_party_query(body.query)
            if body.query
            else (str(body.party_a), str(body.party_b))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return party_cooccurrence(session, party_a, party_b, body.limit)


@app.post("/api/v1/provisions/search")
def provision_lookup(body: ProvisionSearchRequest, session: Session = SessionDep) -> dict:
    try:
        return provision_search(session, body.query, body.limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/passages/search")
def passage_lookup(body: PassageSearchRequest, vector_store: VectorStore = Depends(get_vector_store)) -> dict:
    try:
        results = passage_search(
            vector_store,
            body.query,
            body.limit,
            body.court,
            body.year_from,
            body.year_to,
            body.provision_key,
        )
        return {"query": body.query, "results": results}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Passage retrieval failed: {exc}") from exc


async def _generate_answer(settings: Settings, query: str, passages: list[dict]) -> str:
    context = "\n\n".join(
        f"[{index}] {item.get('case_name', 'Unknown case')} | {item.get('court', '')} | "
        f"page {item.get('page_start', '?')}, paragraphs {item.get('paragraph_start', '?')}-{item.get('paragraph_end', '?')}\n"
        f"{item.get('text', '')}"
        for index, item in enumerate(passages, start=1)
    )
    prompt = (
        "Answer the legal research question only from the judgment passages below. This is research assistance, "
        "not legal advice. Explain when the evidence is insufficient. Cite each factual/legal claim with the passage "
        "number in square brackets, such as [1]. Do not invent cases, holdings, or citations.\n\n"
        f"Question: {query}\n\nJudgment passages:\n{context}"
    )
    base_url = (settings.ollama_url or "http://localhost:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        response = await client.post(
            f"{base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
    return str(response.json().get("message", {}).get("content", "")).strip()


@app.post("/api/v1/rag/ask")
async def ask(
    body: AskRequest,
    settings: Settings = Depends(get_app_settings),
    vector_store: VectorStore = Depends(get_vector_store),
) -> dict:
    try:
        passages = passage_search(vector_store, body.query, body.limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Passage retrieval failed: {exc}") from exc
    answer = None
    answer_error = None
    if settings.ollama_model and passages:
        try:
            answer = await _generate_answer(settings, body.query, passages)
        except (httpx.HTTPError, ValueError) as exc:
            answer_error = f"Ollama answer generation was unavailable: {exc}"
    numbered_passages = [
        {"evidence_number": index, **passage}
        for index, passage in enumerate(passages, start=1)
    ]
    return {"query": body.query, "answer": answer, "answer_error": answer_error, "results": numbered_passages}


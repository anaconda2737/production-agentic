# Judicial RAG prototype

An isolated, local-first prototype for Indian judgment search. It does not import or start the existing arXiv application, OpenSearch, Telegram, Airflow, or Docker Compose. It supports a small judgment corpus with:

- Case lookup by case name, case number, neutral citation, or reporter citation, including fuzzy candidate suggestions.
- “Cited by” results ranked by the number of extracted citation occurrences in each judgment.
- Party-pair co-occurrence search with fuzzy party-name resolution.
- Article and criminal-section mention search.
- Local Qdrant passage retrieval with optional Ollama answer generation.
- Local file storage by default, with an S3-compatible object-storage option.

## 1. Create an isolated environment

From this folder, use Python 3.12 or newer:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The default metadata database is SQLite so a sample can run without Docker or a separate database server. To use PostgreSQL, create a database outside this project and set `JUDICIAL_DATABASE_URL` in `.env`, for example `postgresql+psycopg://user:password@localhost:5432/judicial_rag`.

Qdrant runs in the Python process and persists under `data/qdrant`. This local mode is intended for a single-process prototype. It is not suitable for the full 1 TB corpus or concurrent production serving. For that, configure `JUDICIAL_QDRANT_URL` to point at a separately operated Qdrant server or cluster. No Docker setup is included here.

The first embedding run downloads `BAAI/bge-small-en-v1.5`. The PDF parser extracts embedded text. For scanned PDFs, install Tesseract OCR and set `JUDICIAL_OCR_SCANNED_PDFS=true`.

## 2. Prepare a small sample

Put PDFs under a directory. For reliable case matching, provide a same-name JSON sidecar for each PDF. The sidecar avoids guessing canonical identities from inconsistent judgment headers.

Example `sample/DLHC010051062022.json`:

```json
{
  "case_name": "Example Petitioner v. Example University",
  "case_number": "W.P.(C) 1234/2022",
  "court": "Delhi High Court",
  "decision_date": "2022-08-15",
  "neutral_citation": "2022:DHC:1234",
  "reporter_citations": ["2022 SCC OnLine Del 1234"],
  "parties": [
    {"name": "Example Petitioner", "role": "petitioner"},
    {"name": "Example University", "role": "respondent"}
  ],
  "cited_cases": [],
  "source_uri": "https://example.org/judgment/1234"
}
```

The sidecar `source_uri` is retained as the upstream court/document URL. The `source_uri` returned by the API points to the copied PDF in local object storage or the configured S3-compatible bucket.

The `parties` and `cited_cases` fields may also contain strings. Ingest known target judgments and their citing judgments into the same database so extracted citation occurrences can be resolved to a canonical case. Unresolved extracted references remain stored but do not yet contribute to a case’s cited-by counts.

## 3. Ingest and start the API

```powershell
python -m judicial_rag.cli ingest-folder .\sample
uvicorn judicial_rag.api:app --reload --workers 1
```

Open `http://127.0.0.1:8000/docs` to try the endpoints. Keep one worker while using Qdrant local persistent mode. The first ingestion/search initializes the embedding model and may take a few minutes.

Main endpoints:

- `POST /api/v1/citations/cited-by` with `{"query":"2022:DHC:1234"}`
- `POST /api/v1/parties/cooccurrence` with `{"query":"State of Maharashtra vs H.N. Dhillon"}` (or explicit `party_a` and `party_b` fields)
- `POST /api/v1/provisions/search` with `{"query":"Section 302 IPC"}` or `{"query":"Article 21"}`
- `POST /api/v1/passages/search` with `{"query":"anticipatory bail principles","limit":5}`
- `POST /api/v1/rag/ask` with `{"query":"What did the judgments say about anticipatory bail?"}`

If `JUDICIAL_OLLAMA_MODEL` is configured and Ollama is running locally, `/rag/ask` also asks Ollama to draft a response using the retrieved passages. Otherwise, it returns the evidence passages without a generated answer.

## What this prototype does not claim

This is a functional starting point for a small sample, not a finished legal research product or a ready-to-run 1 TB deployment. Citation and provision extraction use patterns and aliases; Indian judgments have many irregular citation forms, so results need a labeled evaluation set and human review before relying on counts. Party names and metadata should be curated or validated. The parser does not guarantee good OCR/layout recovery. There is no authentication, authorization, audit trail, migration framework, retention policy, or high-availability setup.

Before corpus backfill, build an audited corpus inventory, improve court-specific parsers and entity resolution, add Alembic migrations and operational monitoring, benchmark a representative sample, estimate chunk/vector/object storage, and deploy PostgreSQL, object storage, and Qdrant as independently managed services. Use resumable batches and preserve extraction confidence and provenance for every generated record.

## Storage responsibilities

- **PostgreSQL:** canonical judgments, aliases, party identities, provision identities, and occurrence-level citations/mentions; exact frequency aggregation belongs here.
- **Qdrant:** semantic passage vectors plus filterable judgment metadata.
- **S3/MinIO or local prototype storage:** original PDFs. The prototype copies PDFs locally unless an S3 bucket is configured.

For PostgreSQL fuzzy matching, run `migrations/001_pg_trgm.sql` after connecting to a database where the operator can install `pg_trgm`. SQLite uses an in-process fuzzy matcher for small samples.


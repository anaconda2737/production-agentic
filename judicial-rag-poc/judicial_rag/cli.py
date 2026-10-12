import argparse
import json
import logging
from pathlib import Path

from judicial_rag.config import get_settings
from judicial_rag.db import Database
from judicial_rag.ingestion import ingest_pdf
from judicial_rag.object_store import ObjectStore
from judicial_rag.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Indian judgment PDFs into PostgreSQL/SQLite and Qdrant.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser("ingest-folder", help="Ingest PDF files from a directory tree")
    ingest_parser.add_argument("path", type=Path, help="Folder containing judgment PDFs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.path.is_dir():
        parser.error(f"Not a directory: {args.path}")

    settings = get_settings()
    database = Database(settings.database_url)
    database.create_tables()
    vectors = VectorStore(settings)
    objects = ObjectStore(settings)
    pdf_paths = sorted(args.path.rglob("*.pdf")) + sorted(args.path.rglob("*.PDF"))
    summary = {"pdfs_found": len(pdf_paths), "indexed": 0, "skipped": 0, "failed": 0, "documents": []}
    try:
        for pdf_path in pdf_paths:
            try:
                with database.session() as session:
                    result = ingest_pdf(pdf_path, session, vectors, objects, settings)
                summary["documents"].append(result)
                if result["status"] == "skipped_existing":
                    summary["skipped"] += 1
                else:
                    summary["indexed"] += 1
            except Exception as exc:
                logging.exception("Failed to ingest %s", pdf_path)
                summary["failed"] += 1
                summary["documents"].append({"file": str(pdf_path), "status": "failed", "error": str(exc)})
    finally:
        vectors.close()
        database.close()

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


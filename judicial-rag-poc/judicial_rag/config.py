from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JUDICIAL_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/judicial.db"
    qdrant_path: Path = Path("./data/qdrant")
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "judgment_passages"
    objects_dir: Path = Path("./data/objects")
    s3_bucket: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimension: int = 384
    max_chunk_words: int = 350
    chunk_overlap_words: int = 50
    ocr_scanned_pdfs: bool = False
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_timeout_seconds: int = 180


@lru_cache
def get_settings() -> Settings:
    return Settings()


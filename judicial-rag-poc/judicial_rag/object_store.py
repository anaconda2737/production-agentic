import hashlib
import shutil
from pathlib import Path

from judicial_rag.config import Settings


class ObjectStore:
    """Local prototype store with optional S3/MinIO-compatible uploads."""

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.objects_dir.mkdir(parents=True, exist_ok=True)
        self.s3 = None
        if settings.s3_bucket:
            import boto3

            self.s3 = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
            )

    def put_file(self, source: Path, sha256: str) -> str:
        key = f"judgments/{sha256[:2]}/{sha256}/{source.name}"
        if self.s3:
            self.s3.upload_file(str(source), self.settings.s3_bucket, key)
            return f"s3://{self.settings.s3_bucket}/{key}"

        target = self.settings.objects_dir / sha256[:2] / sha256 / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        return target.resolve().as_uri()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


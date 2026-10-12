from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from judicial_rag.config import Settings


class VectorStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.qdrant_url:
            self.client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                prefer_grpc=True,
            )
        else:
            settings.qdrant_path.parent.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(settings.qdrant_path))
        self._embedder = None
        self.ensure_collection()

    def ensure_collection(self) -> None:
        name = self.settings.qdrant_collection
        if self.client.collection_exists(name):
            return
        self.client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self.settings.embedding_dimension,
                distance=models.Distance.COSINE,
            ),
        )
        for field_name, schema in (
            ("judgment_id", models.PayloadSchemaType.KEYWORD),
            ("court", models.PayloadSchemaType.KEYWORD),
            ("decision_year", models.PayloadSchemaType.INTEGER),
            ("provision_keys", models.PayloadSchemaType.KEYWORD),
        ):
            self.client.create_payload_index(
                collection_name=name,
                field_name=field_name,
                field_schema=schema,
                wait=True,
            )

    @property
    def embedder(self):
        if self._embedder is None:
            from fastembed import TextEmbedding

            self._embedder = TextEmbedding(model_name=self.settings.embedding_model)
        return self._embedder

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self.embedder.embed(texts)]

    @staticmethod
    def point_id(judgment_id: str, chunk_index: int) -> str:
        return str(uuid5(NAMESPACE_URL, f"judicial-rag:{judgment_id}:{chunk_index}"))

    def upsert_chunks(self, judgment_id: str, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0
        vectors = self.embed([chunk["text"] for chunk in chunks])
        points = []
        for chunk, vector in zip(chunks, vectors):
            payload = {key: value for key, value in chunk.items() if key != "text"}
            payload["judgment_id"] = judgment_id
            payload["text"] = chunk["text"]
            points.append(
                models.PointStruct(
                    id=self.point_id(judgment_id, int(chunk["chunk_index"])),
                    vector=vector,
                    payload=payload,
                )
            )
        batch_size = 64 if not self.settings.qdrant_url else 256
        for offset in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points[offset : offset + batch_size],
                wait=True,
            )
        return len(points)

    def delete_judgment(self, judgment_id: str) -> None:
        self.client.delete(
            collection_name=self.settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="judgment_id", match=models.MatchValue(value=judgment_id))])
            ),
            wait=True,
        )

    def search(
        self,
        query: str,
        limit: int = 8,
        court: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        provision_key: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions = []
        if court:
            conditions.append(models.FieldCondition(key="court", match=models.MatchValue(value=court)))
        if year_from is not None or year_to is not None:
            conditions.append(
                models.FieldCondition(
                    key="decision_year",
                    range=models.Range(gte=year_from, lte=year_to),
                )
            )
        if provision_key:
            conditions.append(
                models.FieldCondition(key="provision_keys", match=models.MatchValue(value=provision_key))
            )
        query_filter = models.Filter(must=conditions) if conditions else None
        query_vector = self.embed([query])[0]
        response = self.client.query_points(
            collection_name=self.settings.qdrant_collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {"score": point.score, **(point.payload or {})}
            for point in response.points
        ]

    def health(self) -> dict[str, Any]:
        collection = self.client.get_collection(self.settings.qdrant_collection)
        return {
            "status": "ok",
            "collection": self.settings.qdrant_collection,
            "points_count": collection.points_count,
        }

    def close(self) -> None:
        self.client.close()


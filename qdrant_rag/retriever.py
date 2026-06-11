"""
Qdrant 검색 인터페이스
"""
from dataclasses import dataclass
from typing import Optional

from config import (
    QDRANT_PATH, QDRANT_URL, COLLECTION_NAME,
    EMBED_MODEL, EMBED_BATCH_SIZE, TOP_K,
)

_client = None
_embed_model = None


@dataclass
class SearchResult:
    text: str
    score: float
    file_name: str
    company: str
    company_code: str
    filing_date: str
    report_name: str
    source_type: str
    chunk_index: int


def get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        if QDRANT_URL:
            _client = QdrantClient(url=QDRANT_URL)
        else:
            _client = QdrantClient(path=str(QDRANT_PATH))
    return _client


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def embed_query(query: str) -> list[float]:
    model = get_embed_model()
    vec = model.encode([query], normalize_embeddings=True)
    return vec[0].tolist()


def search(
    query: str,
    top_k: int = TOP_K,
    company_filter: Optional[str] = None,
    source_type_filter: Optional[str] = None,
) -> list[SearchResult]:
    """
    쿼리로 Qdrant 검색. 옵션으로 회사명·파일유형 필터 가능.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    conditions = []
    if company_filter:
        conditions.append(FieldCondition(
            key="company", match=MatchValue(value=company_filter)
        ))
    if source_type_filter:
        conditions.append(FieldCondition(
            key="source_type", match=MatchValue(value=source_type_filter)
        ))

    query_filter = Filter(must=conditions) if conditions else None
    query_vec = embed_query(query)

    client = get_client()
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vec,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    results = []
    for h in response.points:
        p = h.payload
        results.append(SearchResult(
            text=p.get("text", ""),
            score=h.score,
            file_name=p.get("file_name", ""),
            company=p.get("company", ""),
            company_code=p.get("company_code", ""),
            filing_date=p.get("filing_date", ""),
            report_name=p.get("report_name", ""),
            source_type=p.get("source_type", ""),
            chunk_index=p.get("chunk_index", 0),
        ))
    return results


def info() -> dict:
    """컬렉션 상태 반환."""
    client = get_client()
    try:
        cnt = client.count(COLLECTION_NAME).count
        col = client.get_collection(COLLECTION_NAME)
        return {"collection": COLLECTION_NAME, "vectors": cnt,
                "status": col.status.value}
    except Exception as e:
        return {"error": str(e)}

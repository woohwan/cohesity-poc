"""
Qdrant 검색 인터페이스 — dense / BM25 하이브리드 검색 지원
"""
import re
from dataclasses import dataclass
from typing import Optional

from config import (
    QDRANT_PATH, QDRANT_URL, COLLECTION_NAME,
    EMBED_MODEL, EMBED_BATCH_SIZE, TOP_K,
    USE_HYBRID_SEARCH, BM25_MODEL, EMBED_SERVER_URL,
)

_client      = None
_embed_model = None
_bm25_model  = None


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
        import torch
        from sentence_transformers import SentenceTransformer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _embed_model = SentenceTransformer(EMBED_MODEL, device=device)
    return _embed_model


def get_bm25_model():
    global _bm25_model
    if _bm25_model is None:
        from fastembed import SparseTextEmbedding
        _bm25_model = SparseTextEmbedding(model_name=BM25_MODEL)
    return _bm25_model


def _server_post(path: str, payload: dict) -> dict:
    """embed-server에 HTTP POST 요청."""
    import json
    import urllib.request
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{EMBED_SERVER_URL}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def embed_query(query: str) -> list[float]:
    """Dense 벡터 반환 — 서버 우선, 없으면 로컬 모델."""
    if EMBED_SERVER_URL:
        try:
            resp = _server_post("/embed/dense", {"texts": [query], "normalize": True})
            return resp["vectors"][0]
        except Exception as e:
            print(f"[embed-server 연결 실패, 로컬 모델 사용] {e}", flush=True)
    model = get_embed_model()
    return model.encode([query], normalize_embeddings=True)[0].tolist()


def embed_query_sparse(query: str):
    """BM25 Sparse 벡터 반환 — 서버 우선, 없으면 로컬 모델."""
    if EMBED_SERVER_URL:
        try:
            resp = _server_post("/embed/sparse", {"query": query})
            from types import SimpleNamespace
            return SimpleNamespace(indices=resp["indices"], values=resp["values"])
        except Exception as e:
            print(f"[embed-server 연결 실패, 로컬 모델 사용] {e}", flush=True)
    bm25 = get_bm25_model()
    return list(bm25.query_embed(query))[0]


def _extract_date_range(query: str) -> tuple[Optional[str], Optional[str]]:
    """
    쿼리에서 연도/반기/분기를 추출해 (date_from, date_to) 반환.
    예)  "2021년"       → ("20210101", "20211231")
         "2021년 1분기" → ("20210101", "20210331")
         "2021년 상반기" → ("20210101", "20210630")
    """
    year_m = re.search(r'(20\d{2}|19\d{2})년', query)
    if not year_m:
        return None, None

    year = year_m.group(1)

    # 분기
    q_m = re.search(r'([1-4])분기', query)
    if q_m:
        q = int(q_m.group(1))
        month_ranges = {1: ("01", "03"), 2: ("04", "06"),
                        3: ("07", "09"), 4: ("10", "12")}
        m_from, m_to = month_ranges[q]
        return int(f"{year}{m_from}01"), int(f"{year}{m_to}31")

    # 상/하반기
    if "상반기" in query:
        return int(f"{year}0101"), int(f"{year}0630")
    if "하반기" in query:
        return int(f"{year}0701"), int(f"{year}1231")

    # 연도만
    return int(f"{year}0101"), int(f"{year}1231")


def search(
    query: str,
    top_k: int = TOP_K,
    company_filter: Optional[str] = None,
    source_type_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    auto_date: bool = True,
) -> list[SearchResult]:
    """
    쿼리로 Qdrant 검색.
    - auto_date=True: 쿼리에서 연도/분기 자동 감지 → filing_date 필터 적용
    - company_filter: 회사명 일치 필터 (예: "삼성전자")
    - source_type_filter: 파일 유형 (예: "xml", "pdf")
    - date_from / date_to: 공시일 범위 "YYYYMMDD" — auto_date보다 우선
    """
    from qdrant_client.models import (
        Filter, FieldCondition, MatchValue, Range,
        Prefetch, FusionQuery, Fusion, SparseVector,
    )

    # 날짜 범위 결정 (int로 통일 — Qdrant Range는 숫자형만 지원)
    if not date_from and not date_to and auto_date:
        date_from, date_to = _extract_date_range(query)
        if date_from:
            print(f"[검색] 날짜 자동 감지: {date_from} ~ {date_to}")
    if date_from and not isinstance(date_from, int):
        date_from = int(date_from)
    if date_to and not isinstance(date_to, int):
        date_to = int(date_to)

    # 필터 조건 구성
    conditions = []
    if company_filter:
        conditions.append(FieldCondition(
            key="company", match=MatchValue(value=company_filter)
        ))
    if source_type_filter:
        conditions.append(FieldCondition(
            key="source_type", match=MatchValue(value=source_type_filter)
        ))
    if date_from or date_to:
        conditions.append(FieldCondition(
            key="filing_date",
            range=Range(gte=date_from or None, lte=date_to or None),
        ))

    query_filter = Filter(must=conditions) if conditions else None
    client       = get_client()

    # ── 하이브리드 검색 (dense + BM25 RRF) ──────────────────────────────────
    if USE_HYBRID_SEARCH:
        dense_vec  = embed_query(query)
        sparse_vec = embed_query_sparse(query)
        sv = SparseVector(
            indices=list(sparse_vec.indices),
            values=list(sparse_vec.values),
        )
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                Prefetch(query=dense_vec, using="dense",
                         filter=query_filter, limit=top_k * 3),
                Prefetch(query=sv, using="bm25",
                         filter=query_filter, limit=top_k * 3),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
    else:
        # ── Dense 검색만 ─────────────────────────────────────────────────────
        dense_vec = embed_query(query)
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=dense_vec,
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
                "status": col.status.value,
                "hybrid": USE_HYBRID_SEARCH}
    except Exception as e:
        return {"error": str(e)}

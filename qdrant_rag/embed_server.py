"""
임베딩 모델 서버 — 모델을 한 번 로딩 후 HTTP로 서빙

사용법:
  docker compose up -d embed-server    # 서버 시작 (모델 로딩)
  docker compose down embed-server     # 서버 종료

엔드포인트:
  GET  /health           모델 로딩 상태 확인
  POST /embed/dense      텍스트 리스트 → dense 벡터 리스트
  POST /embed/sparse     쿼리 → BM25 sparse 벡터
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from config import EMBED_MODEL, EMBED_BATCH_SIZE, USE_HYBRID_SEARCH, BM25_MODEL, HF_TOKEN

app = FastAPI(title="Embedding Server")

_embed_model = None
_bm25_model  = None


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[EmbedServer] Dense 모델 로딩: {EMBED_MODEL}  (device: {device})", flush=True)
        if device == "cuda":
            print(f"[EmbedServer] GPU: {torch.cuda.get_device_name(0)}", flush=True)
        kwargs = {"token": HF_TOKEN} if HF_TOKEN else {}
        _embed_model = SentenceTransformer(EMBED_MODEL, device=device, **kwargs)
        print("[EmbedServer] Dense 모델 준비 완료", flush=True)
    return _embed_model


def get_bm25_model():
    global _bm25_model
    if _bm25_model is None:
        from fastembed import SparseTextEmbedding
        print(f"[EmbedServer] BM25 모델 로딩: {BM25_MODEL}", flush=True)
        _bm25_model = SparseTextEmbedding(model_name=BM25_MODEL)
        print("[EmbedServer] BM25 모델 준비 완료", flush=True)
    return _bm25_model


@app.on_event("startup")
async def startup():
    get_embed_model()
    if USE_HYBRID_SEARCH:
        get_bm25_model()
    print("[EmbedServer] 준비 완료 — 요청 대기 중", flush=True)


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class DenseRequest(BaseModel):
    texts: list[str]
    normalize: bool = True


class DenseResponse(BaseModel):
    vectors: list[list[float]]


class SparseRequest(BaseModel):
    query: str


class SparseResponse(BaseModel):
    indices: list[int]
    values:  list[float]


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "dense_loaded": _embed_model is not None,
        "bm25_loaded":  _bm25_model  is not None,
        "model": EMBED_MODEL,
        "hybrid": USE_HYBRID_SEARCH,
    }


@app.post("/embed/dense", response_model=DenseResponse)
def embed_dense(req: DenseRequest):
    model = get_embed_model()
    vecs  = model.encode(
        req.texts,
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=req.normalize,
        show_progress_bar=False,
    )
    return DenseResponse(vectors=[v.tolist() for v in vecs])


@app.post("/embed/sparse", response_model=SparseResponse)
def embed_sparse(req: SparseRequest):
    bm25   = get_bm25_model()
    result = list(bm25.query_embed(req.query))[0]
    return SparseResponse(
        indices=result.indices.tolist(),
        values=result.values.tolist(),
    )


if __name__ == "__main__":
    port = int(os.environ.get("EMBED_SERVER_PORT", "8765"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

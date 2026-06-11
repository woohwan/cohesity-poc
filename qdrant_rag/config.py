"""
Qdrant RAG 파이프라인 설정
"""
import os
from pathlib import Path

BASE_DIR         = Path(__file__).parent.parent

# 컨테이너: 환경 변수로 오버라이드, 로컬: 프로젝트 루트 기준
GAIA_DATASET_DIR = Path(os.environ.get("GAIA_DATASET_DIR", str(BASE_DIR / "gaia_dataset")))
GAIA_RAGAS_DIR   = Path(os.environ.get("GAIA_RAGAS_DIR",   str(BASE_DIR / "gaia_ragas")))

# ── Qdrant ────────────────────────────────────────────────────────────────────
# 컨테이너: QDRANT_URL=http://qdrant:6333 (docker-compose에서 자동 설정)
# 로컬:     QDRANT_URL 미설정 시 파일 기반 사용
QDRANT_PATH     = Path(os.environ.get("QDRANT_PATH", str(Path(__file__).parent / "qdrant_storage")))
QDRANT_URL      = os.environ.get("QDRANT_URL", None)
COLLECTION_NAME = "dart_kospi200"

# ── 임베딩 모델 (한국어 특화) ────────────────────────────────────────────────
EMBED_MODEL      = "jhgan/ko-sroberta-multitask"  # 한국어 Sentence-BERT
EMBED_DIMENSION  = 768
EMBED_BATCH_SIZE = 64

# ── 텍스트 청킹 ───────────────────────────────────────────────────────────────
CHUNK_SIZE       = 800    # 글자 수
CHUNK_OVERLAP    = 100
MIN_CHUNK_LENGTH = 50     # 이 길이 미만 청크 제외

# ── 검색 ─────────────────────────────────────────────────────────────────────
TOP_K = 5

# ── Claude API ────────────────────────────────────────────────────────────────
CLAUDE_MODEL    = "claude-sonnet-4-6"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── 인제스트 상태 저장 ─────────────────────────────────────────────────────────
CHECKPOINT_FILE = Path(os.environ.get("CHECKPOINT_FILE",
                       str(Path(__file__).parent / "ingest_checkpoint.json")))

# ── 평가 결과 출력 ─────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(Path(__file__).parent / "output")))

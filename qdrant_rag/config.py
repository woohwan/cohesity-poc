"""
Qdrant RAG 파이프라인 설정
"""
import os
from pathlib import Path

BASE_DIR         = Path(__file__).parent.parent

# 컨테이너: 환경 변수로 오버라이드, 로컬: 프로젝트 루트 기준
GAIA_DATASET_DIR = Path(os.environ.get("GAIA_DATASET_DIR", str(BASE_DIR / "gaia_dataset")))

# ── Qdrant ────────────────────────────────────────────────────────────────────
# 컨테이너: QDRANT_URL=http://qdrant:6333 (docker-compose에서 자동 설정)
# 로컬:     QDRANT_URL 미설정 시 파일 기반 사용
QDRANT_PATH     = Path(os.environ.get("QDRANT_PATH", str(Path(__file__).parent / "qdrant_storage")))
QDRANT_URL      = os.environ.get("QDRANT_URL", None)
COLLECTION_NAME = "dart_kospi200"

# ── 임베딩 모델 (기본값: 한국어 특화, 환경 변수로 오버라이드 가능) ────────────
EMBED_MODEL      = os.environ.get("EMBED_MODEL", "jhgan/ko-sroberta-multitask")
EMBED_DIMENSION  = int(os.environ.get("EMBED_DIMENSION", "768"))
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))
HF_TOKEN         = os.environ.get("HUGGING_FACE_HUB_TOKEN", None)  # private/gated 모델 접근용

# ── 텍스트 청킹 ───────────────────────────────────────────────────────────────
CHUNK_SIZE       = 800    # 글자 수
CHUNK_OVERLAP    = 100
MIN_CHUNK_LENGTH = 50     # 이 길이 미만 청크 제외

# ── 검색 ─────────────────────────────────────────────────────────────────────
TOP_K = 5

# ── Claude API ────────────────────────────────────────────────────────────────
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── 인제스트 상태 저장 (output/ 에 저장 → 컨테이너 재시작 후에도 유지) ──────────
CHECKPOINT_FILE = Path(os.environ.get("CHECKPOINT_FILE",
                       str(Path(os.environ.get("OUTPUT_DIR",
                           str(Path(__file__).parent / "output"))) / "ingest_checkpoint.json")))

# ── QA 생성 설정 ──────────────────────────────────────────────────────────────
SAMPLE_SIZE     = 100    # 샘플 문서 수
QA_PER_DOC      = 2      # 문서당 QA 쌍 수
MIN_TEXT_LENGTH = 300    # 최소 텍스트 길이 (이 미만 문서 제외)
MAX_TEXT_LENGTH = 8000   # Claude 입력용 최대 텍스트 길이

# ── 평가 결과 출력 ─────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(Path(__file__).parent / "output")))

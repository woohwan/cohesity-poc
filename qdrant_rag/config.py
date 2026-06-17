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
import json as _json

CHUNK_SIZE       = int(os.environ.get("CHUNK_SIZE",       "800"))   # 청크 글자 수
CHUNK_OVERLAP    = int(os.environ.get("CHUNK_OVERLAP",    "100"))   # 오버랩 글자 수
MIN_CHUNK_LENGTH = int(os.environ.get("MIN_CHUNK_LENGTH", "50"))    # 이 길이 미만 청크 제외

# 분리 우선순위: 앞쪽부터 순서대로 시도, "" = 글자 단위 (최후 수단)
# JSON 배열 형식으로 .env에 지정:  CHUNK_SEPARATORS=["\n\n","\n","。","."," ",""]
_DEFAULT_SEPARATORS = ["\n\n", "\n", "。", ".", " ", ""]
CHUNK_SEPARATORS = _json.loads(
    os.environ.get("CHUNK_SEPARATORS", _json.dumps(_DEFAULT_SEPARATORS, ensure_ascii=False))
)

# True: 분리자를 청크에 포함 / False: 제거
CHUNK_KEEP_SEPARATOR = os.environ.get("CHUNK_KEEP_SEPARATOR", "false").lower() == "true"

# True: CHUNK_SEPARATORS를 정규식으로 해석
CHUNK_SEPARATOR_REGEX = os.environ.get("CHUNK_SEPARATOR_REGEX", "false").lower() == "true"

# ── 임베딩 서버 (선택) ────────────────────────────────────────────────────────
# 설정 시 retriever가 로컬 모델 대신 서버로 요청 (search/qa 시 모델 로딩 생략)
# docker compose up -d embed-server 로 먼저 시작 필요
EMBED_SERVER_URL = os.environ.get("EMBED_SERVER_URL", None)

# ── 검색 ─────────────────────────────────────────────────────────────────────
TOP_K = int(os.environ.get("TOP_K", "5"))

# 하이브리드 검색 (BM25 sparse + dense 벡터 RRF 퓨전)
# True: 컬렉션에 sparse 벡터 포함 (재인제스트 필요)
# False: dense 벡터만 (기존 방식)
USE_HYBRID_SEARCH = os.environ.get("USE_HYBRID_SEARCH", "true").lower() == "true"
BM25_MODEL        = os.environ.get("BM25_MODEL", "Qdrant/bm25")

# ── LLM API ───────────────────────────────────────────────────────────────────
# LLM_PROVIDER: claude | chatgpt
LLM_PROVIDER      = (os.environ.get("LLM_PROVIDER") or "claude").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
LLM_MODEL         = os.environ.get("LLM_MODEL") or (
    OPENAI_MODEL if LLM_PROVIDER in {"chatgpt", "openai", "gpt"} else CLAUDE_MODEL
)
OPENAI_MAX_OUTPUT_TOKENS = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "4096"))
OPENAI_REASONING_EFFORT  = os.environ.get("OPENAI_REASONING_EFFORT", "minimal")
RAGAS_OPENAI_MODEL       = os.environ.get("RAGAS_OPENAI_MODEL", "gpt-4o-mini")

# ── 인제스트 상태 저장 (output/ 에 저장 → 컨테이너 재시작 후에도 유지) ──────────
CHECKPOINT_FILE = Path(os.environ.get("CHECKPOINT_FILE",
                       str(Path(os.environ.get("OUTPUT_DIR",
                           str(Path(__file__).parent / "output"))) / "ingest_checkpoint.json")))
LOG_FILE     = Path(os.environ.get("OUTPUT_DIR",
                    str(Path(__file__).parent / "output"))) / "ingest.log"
LOG_INTERVAL = int(os.environ.get("LOG_INTERVAL", "1000"))  # 로그 기록 간격 (파일 수)

# ── QA 생성 설정 ──────────────────────────────────────────────────────────────
SAMPLE_SIZE     = 100    # 샘플 문서 수
QA_PER_DOC      = 2      # 문서당 QA 쌍 수
MIN_TEXT_LENGTH = 300    # 최소 텍스트 길이 (이 미만 문서 제외)
MAX_TEXT_LENGTH = 8000   # LLM 입력용 최대 텍스트 길이

# ── 평가 결과 출력 ─────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(Path(__file__).parent / "output")))

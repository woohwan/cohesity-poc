"""
DART GAIA/RAGAS 테스트 설정
"""
import os
from pathlib import Path

# 프로젝트 루트 (컨테이너에서는 /app, 로컬에서는 상위 디렉터리)
BASE_DIR = Path(__file__).parent.parent

# 통합 데이터셋 경로 (컨테이너: 환경 변수, 로컬: gaia_dataset/)
# dataset1/2/3 이 모두 통합된 디렉터리
GAIA_DATASET_DIR = Path(os.environ.get("GAIA_DATASET_DIR",
                         str(BASE_DIR / "gaia_dataset")))

# 원본 데이터셋 경로 (로컬 전용 fallback — GAIA_DATASET_DIR 없을 때)
DATASET_DIRS = {
    "dataset1": Path(os.environ.get("DATASET1_DIR",
                     str(BASE_DIR / "dataset1" / "dart_kospi200_documents"))),
    "dataset2": Path(os.environ.get("DATASET2_DIR",
                     str(BASE_DIR / "dataset2" / "dart_kospi200_rag"))),
    "dataset3": Path(os.environ.get("DATASET3_DIR",
                     str(BASE_DIR / "dataset3" / "dart_kospi200_rag"))),
}

# 출력 경로 (컨테이너: /app/output, 로컬: gaia_ragas/output)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR",
                  str(Path(__file__).parent / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Anthropic API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# 샘플링 설정
SAMPLE_SIZE = 100          # 총 샘플 문서 수
MIN_TEXT_LENGTH = 300      # 최소 텍스트 길이 (너무 짧은 문서 제외)
MAX_TEXT_LENGTH = 8000     # LLM 입력용 최대 텍스트 길이
QA_PER_DOC = 2             # 문서당 생성할 QA 쌍 수

# RAGAS 설정
RAGAS_MIN_SAMPLES = 100    # RAGAS 최소 샘플 수

# 랜덤 시드
RANDOM_SEED = 42

# 보고서 유형 코드 → 한글명 매핑
REPORT_TYPE_MAP = {
    "A": "사업보고서",
    "B": "주요사항보고서",
    "C": "발행공시",
    "D": "지분공시",
    "E": "기타공시",
    "F": "외부감사관련",
    "G": "펀드공시",
    "H": "자산유동화",
    "I": "거래소공시",
    "J": "공정위공시",
}

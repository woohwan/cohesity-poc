"""
Cohesity GAIA 문서 타입별/소스(토픽)별 RAGAS 평가 설정.

collector/gaia_kr_collector 가 모은 gaia_test_200g_kr80_no_ocr 데이터셋에서
문서 타입(pdf/docx_doc/xlsx_xls_csv/ppt_pptx)별 또는 수집 소스(=토픽,
manifest.csv의 source 컬럼)별로 QA/RAGAS 데이터셋을 만든다.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# 수집된 원본 데이터 루트 (collector/gaia_kr_collector/gaia_test_200g_kr80_no_ocr)
DATASET_DIR = Path(os.environ.get(
    "GAIA_KR_DATASET_DIR",
    str(BASE_DIR.parent / "gaia_kr_collector" / "gaia_test_200g_kr80_no_ocr"),
))

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(BASE_DIR / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 확장자 -> 타입 그룹 (gaia_kr_collector/gaia_collect.py TYPE_GROUPS 와 동일하게 유지)
# txt 타입은 제외 — common_crawl 저품질 스크랩 외 실질 문서가 거의 없어 대상에서 뺌
# (2026-07-08 결정).
TYPE_GROUPS = {
    "pdf":          {"pdf"},
    "docx_doc":     {"docx", "doc", "odf", "rtf"},
    "xlsx_xls_csv": {"xlsx", "xls", "csv"},
    "ppt_pptx":     {"ppt", "pptx"},
}
EXT_TO_GROUP = {ext: grp for grp, exts in TYPE_GROUPS.items() for ext in exts}

# LLM API (환경 변수에서 직접 읽음 — gaia_ragas/.env 는 사용하지 않음)
LLM_PROVIDER      = (os.environ.get("LLM_PROVIDER") or "claude").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
LLM_MODEL         = os.environ.get("LLM_MODEL") or (
    OPENAI_MODEL if LLM_PROVIDER in {"chatgpt", "openai", "gpt"} else CLAUDE_MODEL
)
OPENAI_MAX_OUTPUT_TOKENS = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "4096"))
OPENAI_REASONING_EFFORT  = os.environ.get("OPENAI_REASONING_EFFORT", "minimal")

# 샘플링 설정 (타입별 독립 샘플링 — gaia_ragas의 SAMPLE_SIZE=100 관례를 타입 단위로 적용)
SAMPLE_SIZE_PER_TYPE = int(os.environ.get("SAMPLE_SIZE_PER_TYPE", "100"))

# 소스(=수집 사이트/토픽, manifest.csv의 source 컬럼) 단위로 그룹핑할 때 소스당 샘플 수.
# 소스가 80개 안팎으로 타입(4개)보다 훨씬 많아 기본값을 더 작게 잡는다.
SAMPLE_SIZE_PER_SOURCE = int(os.environ.get("SAMPLE_SIZE_PER_SOURCE", "20"))

MIN_TEXT_LENGTH = 300      # 최소 텍스트 길이 (너무 짧은 문서 제외)
MAX_TEXT_LENGTH = 8000     # LLM 입력용 최대 텍스트 길이
QA_PER_DOC = 2             # 문서당 생성할 QA 쌍 수

RANDOM_SEED = 42

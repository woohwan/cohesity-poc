#!/bin/bash
# DART GAIA/RAGAS 파이프라인 실행 스크립트
#
# [A안] 전체 랜덤 샘플링 (회사 무관)
#   ./run.sh                                   전체 파이프라인 (샘플링→QA→테스트셋)
#   ./run.sh --step sample                     문서 샘플링만
#   ./run.sh --step qa                         QA 생성만
#   ./run.sh --step testset                    RAGAS 테스트셋 변환만
#   ./run.sh --step evaluate                   GAIA 평가 (클러스터 필요)
#
# [B안] 특정 회사만 (qdrant_rag 인제스트와 일치시킬 때)
#   ./run.sh --company 삼성전자                 삼성전자 전체 파이프라인
#   ./run.sh --step sample --company 삼성전자  삼성전자 문서 샘플링
#   ./run.sh --step qa     --company 삼성전자  삼성전자 QA 생성
#   → 출력: qa_pairs_삼성전자.json
#
#   ./run.sh consolidate                       문서 통합 (gaia_dataset/ 생성)
#   ./run.sh consolidate --dry-run             통합 미리보기 (복사 없음)
#   ./run.sh consolidate --workers 8           병렬 처리

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"

# 컨테이너 환경이면 시스템 Python 사용, 로컬이면 venv 사용
if [ -f "$VENV" ]; then
    PYTHON="$VENV"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"   # 컨테이너 (시스템 Python)
else
    echo "[ERROR] 가상환경이 없습니다. 아래 명령어로 설치하세요:"
    echo "  python3 -m venv $SCRIPT_DIR/.venv"
    echo "  $SCRIPT_DIR/.venv/bin/pip install -r $SCRIPT_DIR/requirements.txt"
    exit 1
fi

cd "$SCRIPT_DIR"

# 문서 통합 모드
if [ "${1}" = "consolidate" ]; then
    shift
    "$PYTHON" consolidate_docs.py "$@"
    exit $?
fi

# QA 파이프라인 모드 (ANTHROPIC_API_KEY 필요)
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "[ERROR] ANTHROPIC_API_KEY 환경 변수를 설정하세요."
    echo "  export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

"$PYTHON" run_pipeline.py "$@"

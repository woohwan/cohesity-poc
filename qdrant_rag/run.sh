#!/bin/bash
# Qdrant RAG 파이프라인 실행
#
# 사용법:
#   ./run.sh setup                                  가상환경 및 패키지 설치
#   ./run.sh ingest                                 전체 문서 인제스트
#   ./run.sh ingest --limit 500                     테스트용 (500파일)
#   ./run.sh ingest --reset                         컬렉션 초기화 후 재인제스트
#   ./run.sh ingest --company 삼성전자              특정 회사만
#   ./run.sh search "질문"                          벡터 검색 테스트
#   ./run.sh qa "질문"                              RAG Q&A
#   ./run.sh qa "질문" --company LG화학             특정 회사 대상 Q&A
#   ./run.sh evaluate                               RAGAS 평가 (전체)
#   ./run.sh evaluate --limit 20                    RAGAS 평가 (20개)
#   ./run.sh evaluate --company 삼성전자            특정 회사 QA만 평가
#   ./run.sh evaluate --company 삼성전자 --limit 20 회사 필터 + 개수 제한
#   ./run.sh qa-gen                                 QA 쌍 생성 (gaia_dataset → output/)
#   ./run.sh qa-gen --company 삼성전자              특정 회사 QA 생성
#   ./run.sh qa-gen --sample 50 --qa-per-doc 3      샘플 수 / 문서당 QA 수 조정
#   ./run.sh info                                   컬렉션 상태 확인

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"

# ── setup (로컬 전용) ────────────────────────────────────────────────────────
if [ "${1}" = "setup" ]; then
    echo "[setup] 가상환경 생성..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    echo "[setup] 패키지 설치..."
    "$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    echo "[setup] 완료"
    exit 0
fi

# 컨테이너 환경이면 시스템 Python 사용, 로컬이면 venv 사용
if [ -f "$VENV" ]; then
    PYTHON="$VENV"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"   # 컨테이너 (시스템 Python)
else
    echo "[ERROR] 가상환경이 없습니다. 먼저 실행하세요:"
    echo "  ./run.sh setup"
    exit 1
fi

# ANTHROPIC_API_KEY 확인 (qa, qa-gen, evaluate만)
if [[ "${1}" =~ ^(qa|qa-gen|evaluate)$ ]] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "[ERROR] ANTHROPIC_API_KEY 환경 변수를 설정하세요:"
    echo "  export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

cd "$SCRIPT_DIR"

case "${1}" in
    ingest)
        shift
        "$PYTHON" ingest.py "$@"
        ;;
    search)
        shift
        "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from retriever import search
results = search('$1', top_k=5)
for r in results:
    print(f'[{r.score:.4f}] {r.company} | {r.report_name} | {r.filing_date} | {r.source_type.upper()}')
    print(f'  {r.text[:200]}...')
    print()
"
        ;;
    qa)
        shift
        "$PYTHON" qa_chain.py "$@"
        ;;
    evaluate)
        shift
        "$PYTHON" evaluate.py "$@"
        ;;
    qa-gen)
        shift
        "$PYTHON" qa_gen.py "$@"
        ;;
    info)
        "$PYTHON" -c "
import sys; sys.path.insert(0, '.')
from retriever import info
d = info()
for k, v in d.items():
    print(f'  {k}: {v}')
"
        ;;
    *)
        echo "사용법: ./run.sh {setup|ingest|search|qa|qa-gen|evaluate|info} [옵션]"
        echo ""
        echo "  setup                                       가상환경 및 패키지 설치"
        echo "  ingest [--limit N] [--reset] [--company 회사명]"
        echo "  search \"질문\""
        echo "  qa \"질문\" [--company 회사명] [--top-k N]"
        echo "  qa-gen [--sample N] [--qa-per-doc N] [--company 회사명]"
        echo "  evaluate [--limit N] [--company 회사명] [--out 파일명]"
        echo "  info                                        컬렉션 상태"
        echo ""
        echo "  ※ 소규모 테스트 권장 순서:"
        echo "    ./run.sh ingest   --company 삼성전자"
        echo "    ./run.sh qa-gen   --company 삼성전자"
        echo "    ./run.sh evaluate --company 삼성전자 --limit 20"
        exit 1
        ;;
esac

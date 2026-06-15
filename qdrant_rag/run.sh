#!/bin/bash
# Qdrant RAG 파이프라인 실행
#
# 사용법:
#   ./run.sh setup                                         가상환경 및 패키지 설치
#   ./run.sh ingest                                        전체 문서 인제스트
#   ./run.sh ingest --limit 500                            테스트용 (500파일)
#   ./run.sh ingest --reset                                컬렉션 초기화 후 재인제스트
#   ./run.sh ingest --company 삼성전자                     특정 회사만
#   ./run.sh ingest --company 삼성전자 --gpu 2             GPU 2번 사용
#   ./run.sh ingest --workers 8 --gpu 2                   파싱 8스레드 병렬
#   ./run.sh ingest --gpus 2,3,4,5                        멀티 GPU (4개)
#   ./run.sh ingest --gpus 2,3,4,5 --workers 8            멀티 GPU + 파싱 병렬
#   ./run.sh status                                        인제스트 진행 상황 확인
#   ./run.sh search "질문"                                 벡터 검색 테스트
#   ./run.sh qa "질문"                                     RAG Q&A
#   ./run.sh qa "질문" --company LG화학                    특정 회사 대상 Q&A
#   ./run.sh evaluate                                      RAGAS 평가 (전체)
#   ./run.sh evaluate --limit 20                           RAGAS 평가 (20개)
#   ./run.sh evaluate --company 삼성전자                   특정 회사 QA만 평가
#   ./run.sh evaluate --company 삼성전자 --limit 20        회사 필터 + 개수 제한
#   ./run.sh qa-gen                                        QA 쌍 생성 (gaia_dataset → output/)
#   ./run.sh qa-gen --company 삼성전자                     특정 회사 QA 생성
#   ./run.sh qa-gen --sample 50 --qa-per-doc 3             샘플 수 / 문서당 QA 수 조정
#   ./run.sh info                                          컬렉션 상태 확인
#
# 백그라운드 실행 (Docker):
#   docker compose run -d qdrant-rag \
#     ./run.sh ingest --gpus 4,5,6,7 --workers 8
#
#   # 진행 확인
#   tail -f output/ingest.log
#   docker logs -f <container_id>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"

# ── --gpu / --gpus / --workers 옵션 파싱 ─────────────────────────────────────
FILTERED_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --gpu)
            export CUDA_VISIBLE_DEVICES="$2"
            shift 2
            ;;
        --gpus)
            INGEST_GPUS="$2"
            shift 2
            ;;
        --workers)
            INGEST_WORKERS="$2"
            shift 2
            ;;
        *)
            FILTERED_ARGS+=("$1")
            shift
            ;;
    esac
done
set -- "${FILTERED_ARGS[@]}"

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

# ── status: 인제스트 진행 상황 ───────────────────────────────────────────────
if [ "${1}" = "status" ]; then
    LOG="$SCRIPT_DIR/output/ingest.log"
    CKPT="$SCRIPT_DIR/output/ingest_checkpoint.json"

    echo "──────────────────────────────────────────"
    echo " 인제스트 상태"
    echo "──────────────────────────────────────────"

    # 실행 중인 인제스트 컨테이너 확인
    RUNNING=$(docker ps --filter "name=qdrant-rag" --format "{{.ID}} {{.Status}} {{.RunningFor}}" 2>/dev/null)
    if [ -n "$RUNNING" ]; then
        echo "[실행 중] $RUNNING"
    else
        echo "[중지됨] 실행 중인 인제스트 컨테이너 없음"
    fi

    echo ""

    # 체크포인트 파일로 완료 수 집계
    TOTAL_DONE=0
    for F in "$SCRIPT_DIR"/output/ingest_checkpoint*.json; do
        [ -f "$F" ] || continue
        COUNT=$(python3 -c "import json; d=json.load(open('$F')); print(len(d))" 2>/dev/null)
        TOTAL_DONE=$((TOTAL_DONE + COUNT))
        echo "[체크포인트] $(basename "$F"): ${COUNT}개"
    done
    echo "[체크포인트 합계] ${TOTAL_DONE}개 파일 완료"

    echo ""

    # 로그 마지막 10줄
    if [ -f "$LOG" ]; then
        echo "[로그 최근 10줄] $LOG"
        echo "──────────────────────────────────────────"
        tail -10 "$LOG"
    else
        echo "[로그 없음] 아직 인제스트가 시작되지 않았습니다."
    fi

    echo "──────────────────────────────────────────"
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
        WORKERS_ARG=()
        [[ -n "$INGEST_WORKERS" ]] && WORKERS_ARG=(--workers "$INGEST_WORKERS")
        GPUS_ARG=()
        [[ -n "$INGEST_GPUS" ]] && GPUS_ARG=(--gpus "$INGEST_GPUS")
        "$PYTHON" ingest.py "${WORKERS_ARG[@]}" "${GPUS_ARG[@]}" "$@"
        ;;
    search)
        shift
        QUERY="$1"; shift
        SEARCH_COMPANY=""
        SEARCH_FROM=""
        SEARCH_TO=""
        SEARCH_TOPK="$TOP_K"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --company)  SEARCH_COMPANY="$2"; shift 2 ;;
                --from)     SEARCH_FROM="$2";    shift 2 ;;
                --to)       SEARCH_TO="$2";      shift 2 ;;
                --top-k)    SEARCH_TOPK="$2";    shift 2 ;;
                *)          shift ;;
            esac
        done
        "$PYTHON" -c "
import sys; sys.path.insert(0, '.')
from retriever import search
results = search(
    '''$QUERY''',
    top_k=${SEARCH_TOPK:-5},
    company_filter='''$SEARCH_COMPANY''' or None,
    date_from='''$SEARCH_FROM''' or None,
    date_to='''$SEARCH_TO''' or None,
)
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
        echo "사용법: ./run.sh {setup|ingest|status|search|qa|qa-gen|evaluate|info} [옵션]"
        echo ""
        echo "  setup                                       가상환경 및 패키지 설치"
        echo "  ingest [--limit N] [--reset] [--company 회사명] [--gpu N] [--gpus N,N] [--workers N]"
        echo "  status                                      인제스트 진행 상황 (체크포인트 + 로그)"
        echo "  search \"질문\""
        echo "  qa \"질문\" [--company 회사명] [--top-k N]"
        echo "  qa-gen [--sample N] [--qa-per-doc N] [--company 회사명]"
        echo "  evaluate [--limit N] [--company 회사명] [--out 파일명]"
        echo "  info                                        컬렉션 상태"
        echo ""
        echo "  백그라운드 실행:"
        echo "    docker compose run -d qdrant-rag ./run.sh ingest --gpus 4,5,6,7 --workers 8"
        echo "    tail -f output/ingest.log      # 진행 로그 실시간 확인"
        echo "    docker compose run --rm qdrant-rag ./run.sh status   # 상태 요약"
        echo ""
        echo "  ※ 소규모 테스트 권장 순서:"
        echo "    ./run.sh ingest   --company 삼성전자"
        echo "    ./run.sh qa-gen   --company 삼성전자"
        echo "    ./run.sh evaluate --company 삼성전자 --limit 20"
        exit 1
        ;;
esac

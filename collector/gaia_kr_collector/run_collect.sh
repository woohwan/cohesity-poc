#!/usr/bin/env bash
# Gaia 한국어 200GB 데이터 수집 실행 스크립트
# README 기준: PDF/XLSX/CSV/XML/JSON/TXT/DOCX/DOC, archive(zip/bz2) 제외, HWP/HWPX 제외
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 설정 ────────────────────────────────────────────────────────────────────────
CONFIG="config.yaml"
VENV_DIR=".venv"
LOG_DIR="logs"
PID_FILE="$LOG_DIR/collect.pid"
STATE_FILE="$LOG_DIR/collect_state.txt"   # 소스별 완료 상태 기록

# README 단계별 수집 추천 순서
SOURCES=(
  national_assembly_reports
  gov_policy_reports
  kosis_statistics
  data_go_kr
  kowiki_knowledge
  common_crawl_ko_text
  english_reference
)

# ── 도움말 ──────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
사용법: $(basename "$0") <모드> [옵션]

모드:
  --setup           가상환경 생성 및 패키지 설치
  --plan            수집 계획 미리보기 (실제 수집 없음)
  --all             전체 소스 한 번에 수집
  --step            소스별 순차 수집 (README 추천 순서)
  --source <NAME>   특정 소스만 수집

옵션:
  --resume          --step 재실행 시 완료된 소스 건너뜀 (세션 끊김 후 이어서 실행)
  --bg              백그라운드 실행 (세션 끊겨도 계속 실행)
  --status          백그라운드 프로세스 상태 확인
  --stop            백그라운드 프로세스 종료
  --state           각 소스별 수집 완료 상태 출력
  --reset           상태 파일 초기화 (처음부터 다시 수집)
  -h, --help        이 도움말 출력

소스 목록 (단계별 순서):
$(for i in "${!SOURCES[@]}"; do printf '  [%d] %s\n' "$((i+1))" "${SOURCES[$i]}"; done)

용량 목표:
  국회입법조사처/예산정책처      20 GB
  정부/공공기관 정책·연구보고서  45 GB
  KOSIS/통계청                   25 GB
  공공데이터포털                 50 GB
  한국어 Wikipedia               10 GB
  Common Crawl 한국어 텍스트     15 GB
  영어 기준 데이터 (PMC 등)      35 GB
  합계                          200 GB

이어서 실행 예시 (세션 끊김 후):
  $(basename "$0") --step --resume          # 완료된 소스 건너뛰고 이어서
  $(basename "$0") --step --resume --bg     # 백그라운드로 이어서

백그라운드 예시:
  $(basename "$0") --step --bg
  $(basename "$0") --source kowiki_knowledge --bg
  tail -f $LOG_DIR/collect_latest.log
  $(basename "$0") --status
  $(basename "$0") --stop
EOF
  exit 0
}

# ── 로깅 ────────────────────────────────────────────────────────────────────────
LOG_FILE=""
START_TIME=0

ts()     { date '+%Y-%m-%d %H:%M:%S'; }
log()    { echo "[$(ts)] $*"        | tee -a "$LOG_FILE"; }
info()   { echo "[$(ts)] INFO  $*"  | tee -a "$LOG_FILE"; }
warn()   { echo "[$(ts)] WARN  $*"  | tee -a "$LOG_FILE"; }
err()    { echo "[$(ts)] ERROR $*"  | tee -a "$LOG_FILE" >&2; }

elapsed() {
  local secs=$(( $(date +%s) - START_TIME ))
  printf '%02d:%02d:%02d' $((secs/3600)) $((secs%3600/60)) $((secs%60))
}

# ── 상태 파일 관리 ───────────────────────────────────────────────────────────────
# 형식: "source_name done 2025-06-18T14:30:00"
#       "source_name failed 2025-06-18T14:30:00"

state_mark() {
  local src="$1" status="$2"
  mkdir -p "$LOG_DIR"
  # 기존 항목 제거 후 새로 기록
  local tmp; tmp=$(mktemp)
  grep -v "^${src} " "$STATE_FILE" 2>/dev/null >"$tmp" || true
  echo "${src} ${status} $(date '+%Y-%m-%dT%H:%M:%S')" >>"$tmp"
  mv "$tmp" "$STATE_FILE"
}

state_is_done() {
  local src="$1"
  grep -q "^${src} done " "$STATE_FILE" 2>/dev/null
}

state_get() {
  local src="$1"
  grep "^${src} " "$STATE_FILE" 2>/dev/null | awk '{print $2}' || echo "pending"
}

show_state() {
  echo "소스별 수집 상태 ($STATE_FILE)"
  echo "─────────────────────────────────────────────────────"
  for src in "${SOURCES[@]}"; do
    local line; line=$(grep "^${src} " "$STATE_FILE" 2>/dev/null || true)
    if [[ -z "$line" ]]; then
      printf "  %-40s  %s\n" "$src" "pending"
    else
      local status ts_val
      status=$(echo "$line" | awk '{print $2}')
      ts_val=$(echo "$line"  | awk '{print $3}')
      local mark="✗"
      [[ "$status" == "done" ]] && mark="✓"
      printf "  %-40s  %s %s  %s\n" "$src" "$mark" "$status" "$ts_val"
    fi
  done
  echo "─────────────────────────────────────────────────────"
}

reset_state() {
  if [[ -f "$STATE_FILE" ]]; then
    rm -f "$STATE_FILE"
    echo "상태 파일 초기화 완료: $STATE_FILE"
  else
    echo "상태 파일이 없습니다. (이미 초기화 상태)"
  fi
}

# ── 가상환경 설치 ────────────────────────────────────────────────────────────────
setup_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[$(ts)] 가상환경 생성: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  echo "[$(ts)] 패키지 설치 중..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  echo "[$(ts)] 설치 완료."
}

activate_venv() {
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
}

# ── 수집 함수 ────────────────────────────────────────────────────────────────────
do_plan() {
  info "수집 계획 미리보기..."
  python gaia_collect.py --config "$CONFIG" --plan 2>&1 | tee -a "$LOG_FILE"
}

do_source() {
  local src="$1" step="${2:-}" total="${3:-}"
  local label="${step:+[$step/$total] }$src"
  local t0; t0=$(date +%s)

  info "=== 시작: $label ==="
  if python gaia_collect.py --config "$CONFIG" --run "$src" 2>&1 | tee -a "$LOG_FILE"; then
    local secs=$(( $(date +%s) - t0 ))
    info "=== 완료: $label (소요: $(printf '%02d:%02d' $((secs/60)) $((secs%60)))) ==="
    state_mark "$src" "done"
    return 0
  else
    err "=== 실패: $label ==="
    state_mark "$src" "failed"
    return 1
  fi
}

do_all() {
  info "전체 소스 일괄 수집 시작..."
  python gaia_collect.py --config "$CONFIG" --run all 2>&1 | tee -a "$LOG_FILE"
  info "전체 수집 완료. (경과: $(elapsed))"
}

do_step() {
  local resume="$1"   # true | false
  local total=${#SOURCES[@]}
  local failed=() skipped=()

  if [[ "$resume" == true ]]; then
    info "단계별 수집 재개 (완료된 소스 건너뜀)"
  else
    info "단계별 순차 수집 시작 (총 ${total}개 소스)"
  fi

  for i in "${!SOURCES[@]}"; do
    local src="${SOURCES[$i]}"
    local step="$((i+1))"

    if [[ "$resume" == true ]] && state_is_done "$src"; then
      info "=== 건너뜀 (이미 완료): [$step/$total] $src ==="
      skipped+=("$src")
      continue
    fi

    do_source "$src" "$step" "$total" || failed+=("$src")
  done

  info "─────────────────────────────────────────────"
  info "단계별 수집 완료 (경과: $(elapsed))"
  [[ ${#skipped[@]} -gt 0 ]] && info "건너뜀 (이미 완료): ${skipped[*]}"
  if [[ ${#failed[@]} -gt 0 ]]; then
    warn "실패한 소스 (${#failed[@]}개): ${failed[*]}"
    warn "이어서 실행: $(basename "$0") --step --resume"
    warn "특정 소스만: $(basename "$0") --source <NAME>"
    return 1
  fi
  info "모든 소스 성공."
}

# ── 백그라운드 관리 ──────────────────────────────────────────────────────────────
bg_status() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "실행 중인 백그라운드 프로세스가 없습니다."
    echo ""
    show_state
    return 0
  fi
  local pid; pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    local log_path; log_path=$(readlink -f "$LOG_DIR/collect_latest.log" 2>/dev/null || echo "(확인 불가)")
    echo "실행 중 (PID: $pid)"
    echo "로그: $log_path"
    echo "실시간 보기: tail -f $LOG_DIR/collect_latest.log"
    echo ""
    show_state
  else
    echo "PID $pid 프로세스가 이미 종료되었습니다."
    rm -f "$PID_FILE"
    echo ""
    show_state
  fi
}

bg_stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "실행 중인 백그라운드 프로세스가 없습니다."; return 0
  fi
  local pid; pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    echo "프로세스 종료 중 (PID: $pid)..."
    kill "$pid" && rm -f "$PID_FILE" && echo "종료 완료."
  else
    echo "PID $pid 프로세스가 이미 종료되었습니다."
    rm -f "$PID_FILE"
  fi
}

bg_launch() {
  local pass_args=("$@")
  local log_file="$LOG_DIR/collect_$(date +%Y%m%d_%H%M%S).log"
  mkdir -p "$LOG_DIR"

  if [[ -f "$PID_FILE" ]]; then
    local old_pid; old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
      echo "이미 실행 중입니다 (PID: $old_pid). 중지: $(basename "$0") --stop"
      exit 1
    fi
  fi

  nohup bash "$0" "${pass_args[@]}" >"$log_file" 2>&1 &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  ln -sf "$(basename "$log_file")" "$LOG_DIR/collect_latest.log"

  echo "백그라운드 시작"
  echo "  PID  : $pid"
  echo "  로그 : $log_file"
  echo ""
  echo "실시간 로그 : tail -f $LOG_DIR/collect_latest.log"
  echo "상태 확인   : $(basename "$0") --status"
  echo "중지        : $(basename "$0") --stop"
}

# ── 인수 파싱 ────────────────────────────────────────────────────────────────────
[[ $# -eq 0 ]] && usage

MODE=""
SOURCE_NAME=""
BG=false
RESUME=false
FG_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup)   MODE="setup";   FG_ARGS+=("$1") ;;
    --plan)    MODE="plan";    FG_ARGS+=("$1") ;;
    --all)     MODE="all";     FG_ARGS+=("$1") ;;
    --step)    MODE="step";    FG_ARGS+=("$1") ;;
    --resume)  RESUME=true;    FG_ARGS+=("$1") ;;
    --source)
      [[ -z "${2:-}" ]] && { echo "오류: --source 뒤에 소스 이름 필요"; usage; }
      MODE="source"; SOURCE_NAME="$2"
      FG_ARGS+=(--source "$SOURCE_NAME"); shift ;;
    --bg)      BG=true ;;
    --status)  mkdir -p "$LOG_DIR"; bg_status; exit 0 ;;
    --stop)    mkdir -p "$LOG_DIR"; bg_stop;   exit 0 ;;
    --state)   mkdir -p "$LOG_DIR"; show_state; exit 0 ;;
    --reset)   mkdir -p "$LOG_DIR"; reset_state; exit 0 ;;
    -h|--help) usage ;;
    *) echo "알 수 없는 옵션: $1"; usage ;;
  esac
  shift
done

[[ -z "$MODE" ]] && { echo "오류: 모드를 지정하세요."; usage; }

# ── 백그라운드 분기 ──────────────────────────────────────────────────────────────
if [[ "$BG" == true ]]; then
  mkdir -p "$LOG_DIR"
  bg_launch "${FG_ARGS[@]}"
  exit 0
fi

# ── 포그라운드 실행 ──────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/collect_$(date +%Y%m%d_%H%M%S).log"
ln -sf "$(basename "$LOG_FILE")" "$LOG_DIR/collect_latest.log"
START_TIME=$(date +%s)

info "=== Gaia 한국어 200GB 수집기 시작 (모드: $MODE) ==="
info "로그: $LOG_FILE"

setup_venv
activate_venv

case "$MODE" in
  setup)
    info "설치 완료. 종료합니다."
    ;;
  plan)
    do_plan
    ;;
  all)
    do_plan
    do_all
    ;;
  step)
    do_plan
    do_step "$RESUME"
    ;;
  source)
    do_source "$SOURCE_NAME"
    ;;
esac

[[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
info "=== 종료 (총 경과: $(elapsed)) | 로그: $LOG_FILE ==="

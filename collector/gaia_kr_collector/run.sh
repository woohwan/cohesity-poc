#!/bin/bash
# Gaia 한국어 200GB 데이터 수집기 실행 스크립트
#
#   ./run.sh setup               가상환경 생성 및 패키지 설치
#   ./run.sh plan                문서 타입별 수집 현황 출력
#   ./run.sh run [소스...]       포그라운드 수집 (기본: all)
#   ./run.sh bg  [소스...]       백그라운드 수집 (기본: all)
#   ./run.sh status              백그라운드 프로세스 상태 + 로그 확인
#   ./run.sh log                 로그 실시간 출력 (tail -f)
#   ./run.sh stop                백그라운드 프로세스 중단
#   ./run.sh cleanup             임시/중간 파일 정리 (wet_raw, .bin, 이미지)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"
CONFIG="$SCRIPT_DIR/config.yaml"
LOG="$SCRIPT_DIR/collect.log"
PIDFILE="$SCRIPT_DIR/.collect.pid"

# ── Python 경로 결정 ──────────────────────────────────────────────────────────
resolve_python() {
    if [ -f "$VENV" ]; then
        echo "$VENV"
    elif command -v python3 &>/dev/null; then
        echo "python3"
    else
        echo ""
    fi
}

# ── setup ────────────────────────────────────────────────────────────────────
if [ "${1}" = "setup" ]; then
    echo "[setup] 가상환경 생성..."
    rm -rf "$SCRIPT_DIR/.venv"
    python3 -m venv "$SCRIPT_DIR/.venv"
    echo "[setup] 패키지 설치..."
    "$SCRIPT_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

    echo "[setup] LibreOffice HWP 필터 확인..."
    if ! command -v libreoffice &>/dev/null; then
        echo "[setup] LibreOffice 미설치 — HWP/HWPX 변환을 위해 설치합니다..."
        sudo apt install -y libreoffice-core libreoffice-writer libreoffice-h2orestart
    elif ! dpkg -l libreoffice-h2orestart &>/dev/null 2>&1; then
        echo "[setup] libreoffice-h2orestart 미설치 — HWP 필터를 설치합니다..."
        sudo apt install -y libreoffice-h2orestart
    else
        echo "[setup] LibreOffice HWP 필터 확인됨"
    fi

    echo "[setup] 완료"
    exit 0
fi

# ── Python 확인 ───────────────────────────────────────────────────────────────
PYTHON="$(resolve_python)"
if [ -z "$PYTHON" ]; then
    echo "[ERROR] 가상환경이 없습니다. 먼저 실행하세요:"
    echo "  ./run.sh setup"
    exit 1
fi

cd "$SCRIPT_DIR"

# ── plan ─────────────────────────────────────────────────────────────────────
if [ "${1}" = "plan" ]; then
    "$PYTHON" gaia_collect.py --config "$CONFIG" --plan
    exit $?
fi

# ── status ───────────────────────────────────────────────────────────────────
if [ "${1}" = "status" ]; then
    echo "══════════════════════════════════════════════════"
    echo " 수집 상태"
    echo "══════════════════════════════════════════════════"

    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "[실행 중] PID $PID"
            echo "  중단하려면: ./run.sh stop"
        else
            echo "[중지됨] PID $PID (종료됨)"
            rm -f "$PIDFILE"
        fi
    else
        echo "[중지됨] 실행 중인 수집 프로세스 없음"
    fi

    echo ""

    if [ -f "$LOG" ]; then
        echo "[로그 최근 20줄] $LOG"
        echo "──────────────────────────────────────────────────"
        tail -20 "$LOG"
    else
        echo "[로그 없음] 아직 수집이 시작되지 않았습니다."
    fi

    echo ""
    echo "══════════════════════════════════════════════════"
    echo " 타입별 수집 현황"
    echo "══════════════════════════════════════════════════"
    "$PYTHON" gaia_collect.py --config "$CONFIG" --plan
    exit 0
fi

# ── cleanup ──────────────────────────────────────────────────────────────────
if [ "${1}" = "cleanup" ]; then
    ROOT="$SCRIPT_DIR/$(grep '^root_dir:' "$CONFIG" | sed 's|root_dir:[[:space:]]*||;s|^\./||')"
    echo "[cleanup] 임시/중간 파일 정리..."

    # Common Crawl WET 원본 (처리 후 남은 찌꺼기)
    WET_DIR="$ROOT/common_crawl_ko_text/wet_raw"
    if [ -d "$WET_DIR" ]; then
        SIZE=$(du -sh "$WET_DIR" 2>/dev/null | cut -f1)
        rm -rf "$WET_DIR"
        echo "  삭제: wet_raw/ ($SIZE)"
    fi

    # 확장자 불명 .bin 파일
    BIN_COUNT=$(find "$ROOT" -name "*.bin" 2>/dev/null | wc -l)
    if [ "$BIN_COUNT" -gt 0 ]; then
        find "$ROOT" -name "*.bin" -delete
        echo "  삭제: .bin 파일 ${BIN_COUNT}개"
    fi

    # 이미지 파일 (jpg, png, gif 등)
    IMG_COUNT=$(find "$ROOT" \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" -o -name "*.gif" -o -name "*.webp" \) 2>/dev/null | wc -l)
    if [ "$IMG_COUNT" -gt 0 ]; then
        find "$ROOT" \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" -o -name "*.gif" -o -name "*.webp" \) -delete
        echo "  삭제: 이미지 파일 ${IMG_COUNT}개"
    fi

    echo "[cleanup] 완료"
    echo ""
    du -sh "$ROOT" 2>/dev/null
    exit 0
fi

# ── log ──────────────────────────────────────────────────────────────────────
if [ "${1}" = "log" ]; then
    if [ ! -f "$LOG" ]; then
        echo "[로그 없음] $LOG"
        exit 1
    fi
    tail -f "$LOG"
    exit 0
fi

# ── stop ─────────────────────────────────────────────────────────────────────
if [ "${1}" = "stop" ]; then
    if [ ! -f "$PIDFILE" ]; then
        echo "[INFO] 실행 중인 수집 프로세스가 없습니다."
        exit 0
    fi
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "[중단] PID $PID 종료 요청"
        rm -f "$PIDFILE"
    else
        echo "[INFO] PID $PID 는 이미 종료되어 있습니다."
        rm -f "$PIDFILE"
    fi
    exit 0
fi

# ── run (포그라운드) ──────────────────────────────────────────────────────────
if [ "${1}" = "run" ]; then
    shift
    SOURCES="${@:-all}"
    "$PYTHON" gaia_collect.py --config "$CONFIG" --run $SOURCES
    exit $?
fi

# ── bg (백그라운드) ───────────────────────────────────────────────────────────
if [ "${1}" = "bg" ]; then
    shift
    SOURCES="${@:-all}"

    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "[ERROR] 이미 실행 중입니다 (PID $PID)."
            echo "  중단하려면: ./run.sh stop"
            exit 1
        fi
        rm -f "$PIDFILE"
    fi

    nohup "$PYTHON" gaia_collect.py --config "$CONFIG" --run $SOURCES \
        >> "$LOG" 2>&1 &
    PID=$!
    echo $PID > "$PIDFILE"

    echo "[백그라운드 시작] PID $PID"
    echo "  log   : $LOG"
    echo "  확인  : ./run.sh status"
    echo "  로그  : ./run.sh log"
    echo "  중단  : ./run.sh stop"
    exit 0
fi

# ── usage ─────────────────────────────────────────────────────────────────────
echo "사용법: ./run.sh <명령> [소스...]"
echo ""
echo "  setup          가상환경 생성 및 패키지 설치"
echo "  plan           문서 타입별 수집 현황"
echo "  run  [소스...] 포그라운드 수집  (기본: all)"
echo "  bg   [소스...] 백그라운드 수집  (기본: all)"
echo "  status         프로세스 상태 + 로그 + 현황"
echo "  log            로그 실시간 출력 (Ctrl-C로 종료)"
echo "  stop           백그라운드 프로세스 중단"
echo "  cleanup        임시/중간 파일 정리 (wet_raw, .bin, 이미지)"
echo ""
echo "예시:"
echo "  ./run.sh setup"
echo "  ./run.sh bg"
echo "  ./run.sh status"
echo "  ./run.sh bg gov_policy_reports data_go_kr"
echo "  ./run.sh stop"
exit 1

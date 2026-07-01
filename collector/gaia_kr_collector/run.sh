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

    # 프로세스 상태
    RUNNING=0
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            RUNNING=1
        else
            rm -f "$PIDFILE"
        fi
    fi

    if [ -f "$LOG" ]; then
        LOG_MTIME=$(stat -c %Y "$LOG")
        _ROOT_DIR="$SCRIPT_DIR/$(grep '^root_dir:' "$CONFIG" | sed 's|root_dir:[[:space:]]*||;s|^\./||')"
        MAN_MTIME=$(stat -c %Y "$_ROOT_DIR/manifest.csv" 2>/dev/null || echo 0)
        NOW=$(date +%s)
        # 로그와 manifest.csv 중 더 최근 활동 기준으로 경과 시간 계산
        if [ "$MAN_MTIME" -gt "$LOG_MTIME" ]; then
            LAST_MTIME=$MAN_MTIME
            LAST_LABEL="manifest"
        else
            LAST_MTIME=$LOG_MTIME
            LAST_LABEL="로그"
        fi
        DIFF=$((NOW - LAST_MTIME))
        if   [ $DIFF -lt 60 ];    then AGO="${DIFF}초 전"
        elif [ $DIFF -lt 3600 ];  then AGO="$((DIFF / 60))분 전"
        elif [ $DIFF -lt 86400 ]; then AGO="$((DIFF / 3600))시간 전"
        else                           AGO="$((DIFF / 86400))일 전"
        fi
        LOG_TIME=$(stat -c %y "$LOG" | cut -d'.' -f1)

        if [ $RUNNING -eq 1 ]; then
            if [ $DIFF -lt 600 ]; then
                echo "[실행 중 / 활성] PID $PID  — ${LAST_LABEL} ${AGO} 업데이트됨"
            else
                echo "[실행 중 / 정체?] PID $PID  — 마지막 활동 ${AGO} (로그·manifest 모두 정체)"
            fi
            echo "  중단하려면: ./run.sh stop"
        else
            # 종료 이유 추정 (마지막 50줄 기준)
            LAST_SRC=$(grep '=== Running' "$LOG" | tail -1 | sed 's/=== Running \(.*\) ===/\1/')
            LAST_LINES=$(grep -v 'page \[' "$LOG" | grep -v '^$' | tail -50)
            TAIL_ERRORS=$(echo "$LAST_LINES" | grep -iE "^(Error|Exception|Traceback|ValueError|KeyboardInterrupt|Killed)" | tail -3)
            ENDS_WITH_PROGRESS=$(echo "$LAST_LINES" | tail -3 | grep -c 'Overall progress:')

            if grep -q '^\[완료\] 목표 달성' "$LOG" 2>/dev/null; then
                echo "[중지됨 / 목표 달성]  마지막 업데이트: $LOG_TIME ($AGO)"
                echo "  마지막 실행 소스: $LAST_SRC"
            elif grep -q '^\[종료\] 사이클' "$LOG" 2>/dev/null; then
                LAST_CYCLE=$(grep '^\[종료\] 사이클' "$LOG" | tail -1)
                echo "[중지됨 / 소스 소진]  마지막 업데이트: $LOG_TIME ($AGO)"
                echo "  $LAST_CYCLE"
                echo "  → 재시작: ./run.sh bg"
            elif [ "$ENDS_WITH_PROGRESS" -gt 0 ]; then
                MID_ERRORS=$(grep -iE "^(Error|Exception|Traceback|ValueError|KeyboardInterrupt)" "$LOG" | wc -l)
                CYCLE_COUNT=$(grep -c '^════ 사이클' "$LOG" 2>/dev/null || echo 0)
                echo "[중지됨 / 사이클 완료 (목표 미달)]  마지막 업데이트: $LOG_TIME ($AGO)"
                echo "  마지막 실행 소스: $LAST_SRC  |  완료 사이클: $((CYCLE_COUNT / 2))"
                [ "$MID_ERRORS" -gt 0 ] && echo "  (수집 중 에러 ${MID_ERRORS}건 — ./run.sh log 로 확인)"
                echo "  → 재시작: ./run.sh bg"
            elif [ -n "$TAIL_ERRORS" ]; then
                echo "[중지됨 / 에러 종료]  마지막 업데이트: $LOG_TIME ($AGO)"
                echo "  마지막 실행 소스: $LAST_SRC"
                echo "  에러:"
                echo "$TAIL_ERRORS" | sed 's/^/    /'
                echo "  → 재시작: ./run.sh bg"
            else
                echo "[중지됨 / 원인 불명]  마지막 업데이트: $LOG_TIME ($AGO)"
                echo "  마지막 실행 소스: $LAST_SRC"
                echo "  → 재시작: ./run.sh bg"
            fi
        fi
    else
        if [ $RUNNING -eq 1 ]; then
            echo "[실행 중] PID $PID  (아직 로그 없음)"
        else
            echo "[중지됨] 실행 중인 수집 프로세스 없음 / 로그 없음"
        fi
    fi

    echo ""
    echo "══════════════════════════════════════════════════"
    echo " 타입별 수집 현황"
    echo "══════════════════════════════════════════════════"
    "$PYTHON" gaia_collect.py --config "$CONFIG" --plan

    # 디렉토리별 디스크 사용량
    ROOT_DIR="$SCRIPT_DIR/$(grep '^root_dir:' "$CONFIG" | sed 's|root_dir:[[:space:]]*||;s|^\./||')"
    if [ -d "$ROOT_DIR" ]; then
        echo ""
        echo "[디스크] 소스별 수집량:"
        du -sh "$ROOT_DIR"/*/  2>/dev/null | sort -rh | awk '{printf "  %-10s %s\n", $1, $2}'
    fi
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

    # 이번 bg 실행 전체에 대한 로그 파일 하나 생성
    mkdir -p "$SCRIPT_DIR/logs"
    RUN_TS=$(date +%Y%m%d_%H%M%S)
    RUN_LOG="$SCRIPT_DIR/logs/collect_${RUN_TS}.log"
    ln -sf "$RUN_LOG" "$LOG"

    # 목표 달성까지 사이클 반복 (소스 소진 시 자동 종료)
    (
        CYCLE=1
        PREV_COLLECTED="-1"
        STALE_SEC=1800   # 30분 동안 로그·manifest 모두 미업데이트 시 재시작
        MANIFEST="$SCRIPT_DIR/$(grep '^root_dir:' "$CONFIG" | sed 's|root_dir:[[:space:]]*||;s|^\./||')/manifest.csv"
        cd "$SCRIPT_DIR"
        while true; do
            echo "" >> "$RUN_LOG"
            echo "════ 사이클 ${CYCLE} 시작: $(date '+%Y-%m-%d %H:%M:%S') ════" >> "$RUN_LOG"

            # Python을 백그라운드로 실행하고 워치독으로 모니터링
            "$PYTHON" -u gaia_collect.py --config "$CONFIG" --run $SOURCES >> "$RUN_LOG" 2>&1 &
            PYPID=$!
            while kill -0 "$PYPID" 2>/dev/null; do
                sleep 300
                kill -0 "$PYPID" 2>/dev/null || break
                LOG_MT=$(stat -c %Y "$RUN_LOG" 2>/dev/null || echo 0)
                MAN_MT=$(stat -c %Y "$MANIFEST" 2>/dev/null || echo 0)
                LAST_MT=$(( LOG_MT > MAN_MT ? LOG_MT : MAN_MT ))
                NOW_TS=$(date +%s)
                STALE=$((NOW_TS - LAST_MT))
                if [ "$STALE" -gt "$STALE_SEC" ]; then
                    echo "" >> "$RUN_LOG"
                    echo "[워치독] $((STALE/60))분 동안 로그·manifest 미업데이트 → 수집 프로세스 재시작 (PID $PYPID)" >> "$RUN_LOG"
                    kill "$PYPID" 2>/dev/null
                    break
                fi
            done
            wait "$PYPID" 2>/dev/null

            echo "════ 사이클 ${CYCLE} 종료: $(date '+%Y-%m-%d %H:%M:%S') ════" >> "$RUN_LOG"

            # 현재 수집량(bytes) 확인
            NOW_COLLECTED=$("$PYTHON" - << 'PYEOF' 2>/dev/null
import sys; sys.path.insert(0, '.')
from gaia_collect import Cfg, Collector
cfg = Cfg.load('config.yaml')
col = Collector(cfg)
print('DONE' if not col.any_quota_remaining() else str(col.total_collected()))
PYEOF
)
            if [ "$NOW_COLLECTED" = "DONE" ]; then
                echo "[완료] 목표 달성. 수집 종료. $(date '+%Y-%m-%d %H:%M:%S')" >> "$RUN_LOG"
                rm -f "$PIDFILE"
                break
            fi
            if [ "$NOW_COLLECTED" = "$PREV_COLLECTED" ]; then
                echo "[종료] 사이클 ${CYCLE} 후 추가 수집 없음 — 소스 소진. $(date '+%Y-%m-%d %H:%M:%S')" >> "$RUN_LOG"
                rm -f "$PIDFILE"
                break
            fi
            PREV_COLLECTED="$NOW_COLLECTED"
            CYCLE=$((CYCLE + 1))
        done
    ) &
    PID=$!
    echo $PID > "$PIDFILE"

    echo "[백그라운드 시작] PID $PID"
    echo "  log   : $RUN_LOG"
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

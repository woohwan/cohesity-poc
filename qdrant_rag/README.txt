================================================================================
  qdrant_rag — Qdrant 기반 RAG 파이프라인
================================================================================

DART KOSPI200 한국 금융 공시 문서를 Qdrant 벡터 DB에 인제스트하고,
Claude API와 결합해 RAG Q&A 및 RAGAS 품질 평가를 수행하는 파이프라인.

--------------------------------------------------------------------------------
  파일 구조
--------------------------------------------------------------------------------

  qdrant_rag/
  ├── config.py               설정 (경로, 임베딩 모델, Qdrant, Claude)
  ├── ingest.py               문서 파싱 → 청킹 → 임베딩 → Qdrant 저장
  ├── retriever.py            Qdrant 검색 (dense + BM25 하이브리드)
  ├── embed_server.py         임베딩 모델 서버 (모델 1회 로딩 후 HTTP 서빙)
  ├── qa_chain.py             검색 + Claude API Q&A 체인
  ├── qa_gen.py               gaia_dataset 에서 QA 쌍 생성
  ├── evaluate.py             RAGAS 4개 메트릭 평가
  ├── requirements.txt        Python 패키지 목록
  ├── Dockerfile              컨테이너 이미지 정의
  ├── docker-compose.yml      컨테이너 구성 (qdrant, embed-server, qdrant-rag)
  ├── run.sh                  컨테이너 / 로컬 통합 실행 스크립트
  ├── qdrant_storage/         Qdrant 로컬 파일 DB (로컬 모드 시 자동 생성)
  └── output/
      ├── ingest_checkpoint.json        인제스트 진행 상태 (재개용)
      ├── ingest_checkpoint_gpu{N}.json 멀티 GPU 샤드별 체크포인트
      ├── ingest.log                    인제스트 진행 로그 (백그라운드 모니터링용)
      ├── qa_pairs.json                 전체 QA 쌍
      ├── qa_pairs_{회사}.json          회사별 QA 쌍
      ├── qdrant_eval.csv               RAGAS 평가 결과
      └── qdrant_eval.json              RAGAS 점수 요약

--------------------------------------------------------------------------------
  처리 흐름
--------------------------------------------------------------------------------

  gaia_dataset/ (XML/PDF/XLS)
        |
        v
  ingest.py                          <- GPU 직접 사용 (embed-server 미사용)
    dart_xml_parser     ->  텍스트 추출 (스타일/스크립트 노이즈 제거)
    TextSplitter        ->  청킹 (CHUNK_SIZE/CHUNK_OVERLAP 설정 가능)
    Dense 임베딩        ->  GPU 직접 로딩 (멀티 GPU 병렬 처리)
    BM25 sparse 임베딩  ->  USE_HYBRID_SEARCH=true 시 추가
        |
        v
  Qdrant  (dense 벡터 + bm25 sparse 벡터, filing_date는 정수 YYYYMMDD)
        |
        v
  embed-server (상시 기동, Dense + BM25 모델 1회 로딩)
  ├── POST /embed/dense   -> Dense 벡터
  └── POST /embed/sparse  -> BM25 Sparse 벡터
        |
        v
  retriever.py  ->  embed-server HTTP 요청 -> Prefetch(dense+bm25) -> FusionQuery(RRF)
                    쿼리 내 연도/분기 자동 감지 -> filing_date Range 필터 적용
                    (embed-server 미기동 시 로컬 모델로 자동 fallback)
        |
        v
  qa_chain.py   ->  Claude API 답변 생성 (기본: claude-sonnet-4-6)
        |
        v
  evaluate.py   ->  RAGAS 4개 메트릭 평가

[ GPU 사용 분리 ]

  역할          GPU 사용 방식                          설정
  ─────────────────────────────────────────────────────────────────
  ingest        각 GPU 워커 직접 로딩 (멀티 GPU 병렬)  --gpus N,N,...
  embed-server  기동 시 1회 로딩, 이후 상시 서빙       EMBED_SERVER_GPU
  search/qa     embed-server HTTP 요청 (로딩 없음)     -

--------------------------------------------------------------------------------
  사전 조건
--------------------------------------------------------------------------------

  - gaia_dataset/ 디렉터리 존재 (상위 경로 ../gaia_dataset/)
  - ANTHROPIC_API_KEY 환경 변수 (qa, qa-gen, evaluate 명령 시)
  - HUGGING_FACE_HUB_TOKEN 환경 변수 (private/gated HuggingFace 모델 사용 시)
  - NVIDIA GPU 사용 시: NVIDIA Container Toolkit 설치 필요

================================================================================
  Docker 실행 (기본)
================================================================================

[ 1. 사전 설정 ]

  cd qdrant_rag
  cp .env.example .env
  vi .env                         # ANTHROPIC_API_KEY, HOST_UID/HOST_GID, GPU 번호 입력
  docker compose build

[ 2. 서비스 시작 ]

  # Qdrant DB + embed-server 시작 (상시 유지)
  docker compose up -d qdrant embed-server

  # embed-server 로그 확인 (모델 로딩 완료 확인)
  docker compose logs embed-server

  # embed-server 중지
  docker compose stop embed-server

  ※ restart: unless-stopped — 크래시/재부팅 시 자동 재기동
  ※ docker compose stop으로 명시적 중지 시 재부팅 후에도 멈춘 상태 유지

[ 3. 인제스트 ]

  # 전체 인제스트 (177K 파일, 시간 소요)
  docker compose run --rm qdrant-rag ./run.sh ingest

  # 테스트용 소규모
  docker compose run --rm qdrant-rag ./run.sh ingest --limit 500

  # 특정 회사만
  docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자

  # GPU 번호 지정
  docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자 --gpu 7

  # 파싱 병렬화
  docker compose run --rm qdrant-rag ./run.sh ingest --workers 8 --gpu 7

  # 멀티 GPU (GPU당 별도 프로세스, 파일 균등 분배)
  docker compose run --rm qdrant-rag ./run.sh ingest --gpus 2,3,4,5
  docker compose run --rm qdrant-rag ./run.sh ingest --gpus 2,3,4,5 --workers 8

  # 컬렉션 + 체크포인트 초기화 (USE_HYBRID_SEARCH 변경 시 반드시 필요)
  docker compose run --rm qdrant-rag ./run.sh ingest --reset

  ※ 인제스트는 embed-server를 사용하지 않음 — 각 GPU 워커가 직접 모델 로딩

[ 4. 백그라운드 실행 (세션 끊겨도 계속) ]

  docker compose run -d qdrant-rag ./run.sh ingest --gpus 4,5,6,7 --workers 8
  tail -f output/ingest.log                     # 진행 실시간 확인
  docker compose run --rm qdrant-rag ./run.sh status   # 상태 요약

[ 5. 검색 / Q&A (embed-server 기동 중이면 즉시 응답) ]

  # 쿼리 내 연도 자동 감지 → filing_date Range 필터 적용
  docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"

  # 날짜 / 회사 명시 필터
  docker compose run --rm qdrant-rag ./run.sh search "영업이익" \
    --company 삼성전자 --from 20210101 --to 20211231

  docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"
  docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자
  docker compose run --rm qdrant-rag ./run.sh info

[ 6. QA 쌍 생성 / RAGAS 평가 ]

  docker compose run --rm qdrant-rag ./run.sh qa-gen --company 삼성전자
  docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20

================================================================================
  로컬 실행 (선택)
================================================================================

  cd qdrant_rag
  ./run.sh setup
  export ANTHROPIC_API_KEY=sk-ant-...
  export CUDA_VISIBLE_DEVICES=0

  ./run.sh ingest --company 삼성전자
  ./run.sh ingest --gpus 2,3,4,5 --workers 8
  ./run.sh ingest --reset
  ./run.sh status
  ./run.sh search "삼성전자 2021년 영업이익"
  ./run.sh qa "매출액은?" --company 삼성전자
  ./run.sh qa-gen --company 삼성전자
  ./run.sh evaluate --company 삼성전자 --limit 20
  ./run.sh info

================================================================================
  설정 항목 (config.py / 환경 변수)
================================================================================

  변수                     기본값                       환경 변수
  ─────────────────────────────────────────────────────────────────────────────
  GAIA_DATASET_DIR         ../gaia_dataset              GAIA_DATASET_DIR
  QDRANT_URL               None (로컬 파일)             QDRANT_URL
  QDRANT_PATH              ./qdrant_storage             QDRANT_PATH
  COLLECTION_NAME          dart_kospi200                (고정)
  EMBED_MODEL              jhgan/ko-sroberta-multitask  EMBED_MODEL
  EMBED_DIMENSION          768                          EMBED_DIMENSION
  EMBED_BATCH_SIZE         64                           EMBED_BATCH_SIZE
  HF_TOKEN                 None                         HUGGING_FACE_HUB_TOKEN
  EMBED_SERVER_URL         None                         EMBED_SERVER_URL
  USE_HYBRID_SEARCH        true                         USE_HYBRID_SEARCH
  BM25_MODEL               Qdrant/bm25                  BM25_MODEL
  CHUNK_SIZE               800                          CHUNK_SIZE
  CHUNK_OVERLAP            100                          CHUNK_OVERLAP
  MIN_CHUNK_LENGTH         50                           MIN_CHUNK_LENGTH
  CHUNK_SEPARATORS         ["\n\n","\n","。","."," ",""] CHUNK_SEPARATORS
  CHUNK_KEEP_SEPARATOR     false                        CHUNK_KEEP_SEPARATOR
  CHUNK_SEPARATOR_REGEX    false                        CHUNK_SEPARATOR_REGEX
  TOP_K                    5                            TOP_K
  LOG_INTERVAL             1000                         LOG_INTERVAL
  CLAUDE_MODEL             claude-sonnet-4-6            CLAUDE_MODEL
  OUTPUT_DIR               ./output                     OUTPUT_DIR
  (없음)                   1                            CUDA_VISIBLE_DEVICES
  (없음)                   0                            EMBED_SERVER_GPU

  ※ Docker 사용 시 QDRANT_URL=http://qdrant:6333, EMBED_SERVER_URL=http://embed-server:8765 자동 설정
  ※ USE_HYBRID_SEARCH 변경 시 반드시 --reset 재인제스트 필요 (컬렉션 스키마 변경)
  ※ filing_date는 정수(YYYYMMDD)로 저장 — Qdrant Range 필터 사용

================================================================================
  검색 동작 방식
================================================================================

[ 하이브리드 검색 (USE_HYBRID_SEARCH=true) ]

  Dense 벡터와 BM25 sparse 벡터를 RRF(Reciprocal Rank Fusion)으로 결합합니다.
  - Dense: 의미적 유사도 (한국어 문맥 이해)
  - BM25 : 키워드 정밀도 (회사명, 수치, 고유명사)
  - RRF  : 두 결과의 순위를 결합해 최종 순위 결정

[ 날짜 자동 감지 ]

  쿼리에 연도/분기 표현이 있으면 filing_date 정수 Range 필터를 자동 적용합니다.

  "삼성전자 2021년 영업이익"      ->  20210101 ~ 20211231
  "LG화학 2022년 1분기 매출"     ->  20220101 ~ 20220331
  "현대차 2020년 상반기 실적"    ->  20200101 ~ 20200630
  "SK하이닉스 2023년 하반기 투자" ->  20230701 ~ 20231231

  --from / --to 명시 시 자동 감지보다 우선 적용됩니다.

================================================================================
  RAGAS 평가 메트릭 및 목표 기준
================================================================================

  메트릭               목표    설명
  ──────────────────────────────────────────────────────────────────────
  faithfulness         >= 0.70  답변이 검색 문서에 근거하는가
  answer_relevancy     >= 0.60  답변이 질문과 관련있는가
  context_precision    >= 0.50  검색된 문서가 관련있는가
  context_recall       >= 0.50  관련 문서를 충분히 검색했는가

================================================================================
  참고 사항
================================================================================

  - embed-server: Dense + BM25 모델을 한 번 로딩 후 HTTP 상시 서빙
    EMBED_SERVER_GPU로 전용 GPU 지정 (인제스트 GPU와 분리 권장)
    미기동 시 retriever가 로컬 모델로 자동 fallback
  - ingest는 embed-server 미사용 — 각 GPU 워커가 직접 로딩하여 병렬 처리
  - --gpu N       : 단일 GPU 지정
  - --gpus N,N,...: 멀티 GPU (GPU당 spawn 프로세스, CUDA fork 이슈 없음)
  - --workers N   : 파일 파싱 병렬 스레드 수 (8~16 권장)
  - output/ingest.log: 백그라운드 인제스트 시 tail -f 로 실시간 확인 가능
  - ./run.sh status: 체크포인트 집계 + 로그 최근 10줄 요약
  - 인제스트 중단 시 체크포인트 기반으로 이어서 재개 가능
  - --reset 실행 시 컬렉션과 체크포인트 파일 전체 삭제
  - QA 생성 중단 시 output/qa_pairs.json 기반으로 이어서 재개 가능
  - 컨테이너는 HOST_UID/HOST_GID로 실행 (볼륨 파일 권한 문제 없음)
  - gaia_ragas와는 gaia_dataset/ 만 공유하며 완전 독립 실행 가능

================================================================================

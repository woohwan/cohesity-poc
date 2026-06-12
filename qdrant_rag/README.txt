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
  ├── retriever.py            Qdrant 벡터 검색 인터페이스
  ├── qa_chain.py             검색 + Claude API Q&A 체인
  ├── qa_gen.py               gaia_dataset 에서 QA 쌍 생성
  ├── evaluate.py             RAGAS 4개 메트릭 평가
  ├── requirements.txt        Python 패키지 목록
  ├── Dockerfile              컨테이너 이미지 정의
  ├── run.sh                  컨테이너 / 로컬 통합 실행 스크립트
  ├── qdrant_storage/         Qdrant 로컬 파일 DB (로컬 모드 시 자동 생성)
  └── output/
      ├── ingest_checkpoint.json   인제스트 진행 상태 (재개용)
      ├── qa_pairs.json            전체 QA 쌍
      ├── qa_pairs_{회사}.json     회사별 QA 쌍
      ├── qdrant_eval.csv          RAGAS 평가 결과
      └── qdrant_eval.json         RAGAS 점수 요약

--------------------------------------------------------------------------------
  처리 흐름
--------------------------------------------------------------------------------

  gaia_dataset/ (XML/PDF/XLS)
        |
        v
  ingest.py
    dart_xml_parser     ->  텍스트 추출
    TextSplitter        ->  청킹 (800자, overlap 100)
    ko-sroberta         ->  임베딩 (768차원)
        |
        v
  Qdrant (Docker 컨테이너 or 로컬 파일)
        |
        v
  retriever.py  ->  벡터 검색 (query_points)
        |
        v
  qa_chain.py   ->  Claude API 답변 생성 (기본: claude-sonnet-4-6)
        |
        v
  evaluate.py   ->  RAGAS 4개 메트릭 평가

--------------------------------------------------------------------------------
  사전 조건
--------------------------------------------------------------------------------

  - gaia_dataset/ 디렉터리 존재 (상위 경로 ../gaia_dataset/)
  - ANTHROPIC_API_KEY 환경 변수 (qa, qa-gen, evaluate 명령 시)
  - HUGGING_FACE_HUB_TOKEN 환경 변수 (private/gated HuggingFace 모델 사용 시)
  - gaia_ragas와는 gaia_dataset/ 만 공유하며 완전 독립 실행 가능

================================================================================
  Docker 실행 (기본)
================================================================================

  프로젝트 루트(cohesity-poc/)의 docker-compose.yml 사용

[ 1. 사전 설정 ]

  cd cohesity-poc
  cp .env.example .env
  vi .env                         # ANTHROPIC_API_KEY, HUGGING_FACE_HUB_TOKEN 입력
  docker compose build
  docker compose up -d qdrant     # Qdrant 컨테이너 시작

[ 2. 인제스트 ]

  # 전체 인제스트 (177K 파일, 시간 소요)
  docker compose run --rm qdrant-rag ./run.sh ingest

  # 테스트용 소규모
  docker compose run --rm qdrant-rag ./run.sh ingest --limit 500

  # 특정 회사만
  docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자

  # 컬렉션 초기화 후 재인제스트
  docker compose run --rm qdrant-rag ./run.sh ingest --reset

[ 3. 검색 / Q&A 테스트 ]

  docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"
  docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"
  docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자
  docker compose run --rm qdrant-rag ./run.sh info

[ 4. QA 쌍 생성 ]

  # 전체 100개 샘플 → output/qa_pairs.json
  docker compose run --rm qdrant-rag ./run.sh qa-gen

  # 특정 회사 → output/qa_pairs_삼성전자.json
  docker compose run --rm qdrant-rag ./run.sh qa-gen --company 삼성전자

  # 샘플 수 / 문서당 QA 수 조정
  docker compose run --rm qdrant-rag ./run.sh qa-gen --sample 50 --qa-per-doc 3

[ 5. RAGAS 평가 ]

  # 전체 평가 (output/qa_pairs.json 사용)
  docker compose run --rm qdrant-rag ./run.sh evaluate

  # 특정 회사, 20개 제한 (output/qa_pairs_삼성전자.json 자동 사용)
  docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20

================================================================================
  로컬 실행 (선택)
================================================================================

  Docker 없이 로컬 .venv에서 직접 실행하는 방법.

[ 1. 환경 설정 ]

  cd qdrant_rag
  ./run.sh setup                          # .venv 생성 및 패키지 설치
  export ANTHROPIC_API_KEY=sk-ant-...
  export HUGGING_FACE_HUB_TOKEN=hf_...   # 필요 시

[ 2. 실행 (명령어는 Docker와 동일) ]

  ./run.sh ingest --company 삼성전자
  ./run.sh qa-gen --company 삼성전자
  ./run.sh evaluate --company 삼성전자 --limit 20
  ./run.sh search "삼성전자 2021년 영업이익"
  ./run.sh qa "매출액은?" --company 삼성전자
  ./run.sh info

  ※ Qdrant는 로컬 파일 모드(qdrant_storage/)로 자동 실행됩니다.
  ※ 20,000 벡터 초과 시 성능 경고 발생 — 대규모 테스트는 Docker 모드 권장.

================================================================================
  설정 항목 (config.py)
================================================================================

  변수                기본값                      환경 변수
  ─────────────────────────────────────────────────────────────────────────
  GAIA_DATASET_DIR    ../gaia_dataset             GAIA_DATASET_DIR
  QDRANT_URL          None (로컬 파일)            QDRANT_URL
  QDRANT_PATH         ./qdrant_storage            QDRANT_PATH
  COLLECTION_NAME     dart_kospi200               (고정)
  EMBED_MODEL         jhgan/ko-sroberta-multitask EMBED_MODEL
  EMBED_DIMENSION     768                         EMBED_DIMENSION
  HF_TOKEN            None                        HUGGING_FACE_HUB_TOKEN
  CHUNK_SIZE          800                         (고정)
  CHUNK_OVERLAP       100                         (고정)
  TOP_K               5                           (고정)
  CLAUDE_MODEL        claude-sonnet-4-6           CLAUDE_MODEL
  OUTPUT_DIR          ./output                    OUTPUT_DIR

  ※ Docker 사용 시 QDRANT_URL=http://qdrant:6333 자동 설정 (docker-compose.yml)

================================================================================
  RAGAS 평가 메트릭 및 목표 기준
================================================================================

  메트릭               목표    설명
  ──────────────────────────────────────────────────────────────────────
  faithfulness         >= 0.70  답변이 검색 문서에 근거하는가
  answer_relevancy     >= 0.60  답변이 질문과 관련있는가
  context_precision    >= 0.50  검색된 문서가 관련있는가
  context_recall       >= 0.50  관련 문서를 충분히 검색했는가

  평가 결과 저장 위치:
    output/qdrant_eval.csv   (상세 결과)
    output/qdrant_eval.json  (점수 요약)

================================================================================
  참고 사항
================================================================================

  - 임베딩 모델 최초 실행 시 HuggingFace 자동 다운로드
  - private/gated 모델 사용 시 HUGGING_FACE_HUB_TOKEN 설정 필요
  - 인제스트 중단 시 output/ingest_checkpoint.json 기반으로 이어서 재개 가능
  - QA 생성 중단 시 output/qa_pairs.json 기반으로 이어서 재개 가능 (doc_id 기준)
  - 로컬 Qdrant 파일 모드에서 20,000 벡터 초과 시 성능 경고 발생
    → 대규모 테스트 시 Docker 모드(QDRANT_URL 설정) 권장
  - gaia_ragas와는 gaia_dataset/ 만 공유하며 완전 독립 실행 가능

================================================================================

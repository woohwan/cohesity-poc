# qdrant_rag — Qdrant 기반 RAG 파이프라인

DART KOSPI200 한국 금융 공시 문서를 Qdrant 벡터 DB에 인제스트하고,
Claude API와 결합해 RAG Q&A 및 RAGAS 품질 평가를 수행하는 파이프라인.

## 파일 구조

```
qdrant_rag/
├── config.py               설정 (경로, 임베딩 모델, Qdrant, Claude)
├── ingest.py               문서 파싱 → 청킹 → 임베딩 → Qdrant 저장
├── retriever.py            Qdrant 벡터 검색 인터페이스
├── qa_chain.py             검색 + Claude API Q&A 체인
├── qa_gen.py               gaia_dataset 에서 QA 쌍 생성
├── evaluate.py             RAGAS 4개 메트릭 평가
├── requirements.txt        Python 패키지 목록
├── Dockerfile              컨테이너 이미지 정의
├── docker-compose.yml      컨테이너 구성 (qdrant_rag 단독 실행)
├── .env.example            환경 변수 템플릿
├── run.sh                  컨테이너 / 로컬 통합 실행 스크립트
├── qdrant_storage/         Qdrant 로컬 파일 DB (로컬 모드 시 자동 생성)
└── output/
    ├── ingest_checkpoint.json   인제스트 진행 상태 (재개용)
    ├── qa_pairs.json            전체 QA 쌍
    ├── qa_pairs_{회사}.json     회사별 QA 쌍
    ├── qdrant_eval.csv          RAGAS 평가 결과
    └── qdrant_eval.json         RAGAS 점수 요약
```

## 아키텍처

```
gaia_dataset/ (XML/PDF/XLS)
      │
      ▼
  ingest.py
  dart_xml_parser  →  텍스트 추출
  RecursiveCharacterTextSplitter  →  청킹 (800자, overlap 100)
  임베딩 모델 (기본: jhgan/ko-sroberta-multitask, 768차원)  →  .env의 EMBED_MODEL로 변경 가능
      │
      ▼
  Qdrant (Docker 컨테이너 or 로컬 파일)
      │
      ▼
  retriever.py  →  query_points() 벡터 검색
      │
      ▼
  qa_chain.py  →  Claude API 답변 생성 (기본: claude-sonnet-4-6, CLAUDE_MODEL로 변경 가능)
      │
      ▼
  evaluate.py  →  RAGAS 4개 메트릭 평가
```

## 사전 조건

- `gaia_dataset/` 디렉터리 존재 (상위 경로 `../gaia_dataset/`)
- `ANTHROPIC_API_KEY` 환경 변수 (`qa`, `qa-gen`, `evaluate` 명령 시)
- `HUGGING_FACE_HUB_TOKEN` 환경 변수 (private/gated HuggingFace 모델 사용 시)

---

## Docker 실행 (기본)

`qdrant_rag/` 디렉터리 안의 `docker-compose.yml` 사용 — `gaia_ragas`와 독립 실행.

### 1. 사전 설정

```bash
cd qdrant_rag
cp .env.example .env
vi .env   # ANTHROPIC_API_KEY 입력, UID/GID 확인 (id -u && id -g)

docker compose build
docker compose up -d qdrant   # Qdrant 컨테이너 시작
```

`.env` 주요 항목:

```
ANTHROPIC_API_KEY=sk-ant-...
HUGGING_FACE_HUB_TOKEN=        # private/gated 모델 사용 시 입력

# 임베딩 모델 (모델 변경 시 EMBED_DIMENSION도 반드시 함께 수정)
EMBED_MODEL=jhgan/ko-sroberta-multitask
EMBED_DIMENSION=768

# 컨테이너 실행 사용자 (호스트 사용자와 일치시켜 볼륨 권한 문제 방지)
UID=1000
GID=1000
```

### 2. 인제스트

```bash
# 전체 인제스트 (177K 파일, 시간 소요)
docker compose run --rm qdrant-rag ./run.sh ingest

# 테스트용 소규모 (500파일)
docker compose run --rm qdrant-rag ./run.sh ingest --limit 500

# 특정 회사만
docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자

# 컬렉션 + 체크포인트 완전 초기화 후 재인제스트
docker compose run --rm qdrant-rag ./run.sh ingest --reset
```

> `--reset`은 Qdrant 컬렉션을 삭제하고 `output/ingest_checkpoint.json`도 함께 삭제합니다.

### 3. 검색 / Q&A 테스트

```bash
# 벡터 검색
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"

# RAG Q&A
docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"
docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자

# 컬렉션 상태 확인
docker compose run --rm qdrant-rag ./run.sh info
```

### 4. QA 쌍 생성

```bash
# 전체 100개 샘플 → output/qa_pairs.json
docker compose run --rm qdrant-rag ./run.sh qa-gen

# 특정 회사 → output/qa_pairs_삼성전자.json
docker compose run --rm qdrant-rag ./run.sh qa-gen --company 삼성전자

# 샘플 수 / 문서당 QA 수 조정
docker compose run --rm qdrant-rag ./run.sh qa-gen --sample 50 --qa-per-doc 3
```

### 5. RAGAS 평가

```bash
# 전체 평가 (output/qa_pairs.json 사용)
docker compose run --rm qdrant-rag ./run.sh evaluate

# 특정 회사, 20개 제한 (output/qa_pairs_삼성전자.json 자동 사용)
docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20
```

---

## 로컬 실행 (선택)

Docker 없이 로컬 `.venv`에서 직접 실행하는 방법.

### 1. 환경 설정

```bash
cd qdrant_rag
./run.sh setup   # .venv 생성 및 패키지 설치
export ANTHROPIC_API_KEY=sk-ant-...
export HUGGING_FACE_HUB_TOKEN=hf_...   # 필요 시
export EMBED_MODEL=jhgan/ko-sroberta-multitask   # 필요 시 변경
export EMBED_DIMENSION=768                       # 모델 변경 시 함께 수정
```

### 2. 실행 (명령어는 Docker와 동일)

```bash
./run.sh ingest --company 삼성전자
./run.sh ingest --reset                          # 완전 초기화 후 재인제스트
./run.sh qa-gen --company 삼성전자
./run.sh evaluate --company 삼성전자 --limit 20
./run.sh search "삼성전자 2021년 영업이익"
./run.sh qa "매출액은?" --company 삼성전자
./run.sh info
```

> Qdrant는 로컬 파일 모드(`qdrant_storage/`)로 자동 실행됩니다.  
> 20,000 벡터 초과 시 성능 경고 발생 — 대규모 테스트는 Docker 모드 권장.

---

## 설정 (config.py)

| 변수 | 기본값 | 환경 변수 | 설명 |
|------|--------|-----------|------|
| `GAIA_DATASET_DIR` | `../gaia_dataset` | `GAIA_DATASET_DIR` | 인제스트 대상 데이터 경로 |
| `QDRANT_URL` | `None` (로컬 파일) | `QDRANT_URL` | Docker 시 `http://qdrant:6333` |
| `QDRANT_PATH` | `./qdrant_storage` | `QDRANT_PATH` | 로컬 파일 DB 경로 |
| `COLLECTION_NAME` | `dart_kospi200` | — | Qdrant 컬렉션 이름 |
| `EMBED_MODEL` | `jhgan/ko-sroberta-multitask` | `EMBED_MODEL` | 임베딩 모델 |
| `EMBED_DIMENSION` | `768` | `EMBED_DIMENSION` | 임베딩 벡터 차원 (모델 변경 시 함께 수정) |
| `HF_TOKEN` | `None` | `HUGGING_FACE_HUB_TOKEN` | HuggingFace private/gated 모델 접근 토큰 |
| `CHUNK_SIZE` | `800` | — | 청크 글자 수 |
| `CHUNK_OVERLAP` | `100` | — | 청크 오버랩 글자 수 |
| `TOP_K` | `5` | — | 검색 반환 문서 수 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | `CLAUDE_MODEL` | Q&A / 평가 LLM |
| `OUTPUT_DIR` | `./output` | `OUTPUT_DIR` | 결과 저장 디렉터리 |

---

## RAGAS 평가 메트릭

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `faithfulness` | ≥ 0.70 | 답변이 검색 문서에 근거하는가 |
| `answer_relevancy` | ≥ 0.60 | 답변이 질문과 관련있는가 |
| `context_precision` | ≥ 0.50 | 검색된 문서가 관련있는가 |
| `context_recall` | ≥ 0.50 | 관련 문서를 충분히 검색했는가 |

평가 결과는 `output/qdrant_eval.csv` 와 `output/qdrant_eval.json` 으로 저장됩니다.

---

## 참고

- 임베딩 모델 최초 실행 시 HuggingFace 자동 다운로드
  - **Docker**: `~/.cache/huggingface/` (호스트) ↔ `/app/hf_cache` (컨테이너) 볼륨 마운트로 공유
  - **로컬**: `~/.cache/huggingface/hub/`
- private/gated 모델 사용 시 `HUGGING_FACE_HUB_TOKEN` 설정 필요
- 인제스트 중단 시 `output/ingest_checkpoint.json` 기반으로 이어서 재개 가능
- `--reset` 실행 시 Qdrant 컬렉션과 체크포인트 파일 모두 삭제되어 처음부터 재인제스트
- QA 생성 중단 시 `output/qa_pairs.json` 기반으로 이어서 재개 가능 (doc_id 기준)
- 컨테이너는 호스트 사용자(`UID`/`GID`)로 실행되어 볼륨 마운트 파일 권한 문제가 없음
- `gaia_ragas`와는 `gaia_dataset/` 만 공유하며 완전 독립 실행 가능

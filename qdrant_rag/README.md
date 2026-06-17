# qdrant_rag — Qdrant 기반 RAG 파이프라인

DART KOSPI200 한국 금융 공시 문서를 Qdrant 벡터 DB에 인제스트하고,
LLM API와 결합해 RAG Q&A 및 RAGAS 품질 평가를 수행하는 파이프라인.

## 현재 인덱스 데이터 범위

- 현재 Qdrant 컬렉션(`dart_kospi200`)에 인제스트된 `filing_date` 범위: **2015-02-13 ~ 2026-05-08**
- 원본 `gaia_dataset/` 파일명 기준 날짜 범위도 **2015년 ~ 2026년**입니다.
- 예: `삼성전자 2000년 영업이익`은 현재 인덱스 범위 밖이라 검색 결과가 0건입니다.
- 연간 재무 질의(`영업이익`, `매출`, `순이익`, `사업보고서`, `감사보고서` 등)는 사업연도로 해석합니다.
  예: `삼성전자 2015년 영업이익` -> `2015-01-01 ~ 2016-04-30` 공시까지 검색
- 분기/반기 표현은 해당 기간의 `filing_date` 범위로 검색합니다.


## 파일 구조

```
qdrant_rag/
├── config.py               설정 (경로, 임베딩 모델, Qdrant, LLM)
├── ingest.py               문서 파싱 → 청킹 → 임베딩 → Qdrant 저장
├── retriever.py            Qdrant 벡터 검색 인터페이스 (dense + BM25 하이브리드)
├── embed_server.py         임베딩 모델 서버 (모델 1회 로딩 후 HTTP 서빙)
├── qa_chain.py             검색 + LLM API Q&A 체인
├── qa_gen.py               gaia_dataset 에서 QA 쌍 생성
├── evaluate.py             RAGAS 4개 메트릭 평가
├── requirements.txt        Python 패키지 목록
├── Dockerfile              컨테이너 이미지 정의
├── docker-compose.yml      컨테이너 구성 (qdrant, embed-server, qdrant-rag)
├── .env.example            환경 변수 템플릿
├── run.sh                  컨테이너 / 로컬 통합 실행 스크립트
├── qdrant_storage/         Qdrant 로컬 파일 DB (로컬 모드 시 자동 생성)
└── output/
    ├── ingest_checkpoint.json        인제스트 진행 상태 (재개용)
    ├── ingest_checkpoint_gpu{N}.json 멀티 GPU 샤드별 체크포인트 (자동 생성)
    ├── ingest.log                    인제스트 진행 로그 (백그라운드 모니터링용)
    ├── qa_pairs.json                 전체 QA 쌍
    ├── qa_pairs_{회사}.json          회사별 QA 쌍
    ├── qdrant_eval.csv               RAGAS 평가 결과
    └── qdrant_eval.json              RAGAS 점수 요약
```

## 아키텍처

```
gaia_dataset/ (XML/PDF/XLS)
      │
      ▼
  ingest.py                           ← GPU 직접 사용 (embed-server 미사용)
  dart_xml_parser  →  텍스트 추출 (스타일/스크립트 노이즈 제거)
  RecursiveCharacterTextSplitter  →  청킹 (.env의 CHUNK_SIZE / CHUNK_OVERLAP 설정 가능)
  Dense 임베딩 (SentenceTransformer)  →  GPU 직접 로딩
  BM25 sparse 임베딩 (fastembed)      →  USE_HYBRID_SEARCH=true 시 추가
      │
      ▼
  Qdrant (Docker 컨테이너 or 로컬 파일)
  ├── dense  벡터 (named vector)
  └── bm25   sparse 벡터 (named vector, 하이브리드 모드)
      │
      ▼
  embed-server (별도 서비스, 모델 1회 로딩 후 상시 대기)
  ├── POST /embed/dense   → Dense 벡터
  └── POST /embed/sparse  → BM25 Sparse 벡터
      │
      ▼
  retriever.py  →  embed-server에 HTTP 요청 → Prefetch(dense+bm25) → FusionQuery(RRF)
                   쿼리 내 연도/분기 자동 감지 → filing_date 필터 적용
                   (embed-server 미기동 시 로컬 모델로 자동 fallback)
      │
      ▼
  qa_chain.py  →  LLM API 답변 생성 (LLM_PROVIDER/LLM_MODEL로 변경 가능)
      │
      ▼
  evaluate.py  →  RAGAS 4개 메트릭 평가
```

### GPU 사용 분리

| 역할 | GPU 사용 방식 | 설정 |
|------|--------------|------|
| **ingest** | 각 GPU 워커가 직접 로딩 (멀티 GPU 병렬) | `--gpus N,N,...` 또는 `CUDA_VISIBLE_DEVICES` |
| **embed-server** | 서버 기동 시 1회 로딩, 이후 상시 서빙 | `EMBED_SERVER_GPU` |
| **search / qa** | embed-server에 HTTP 요청 (모델 로딩 없음) | — |

## 사전 조건

- `gaia_dataset/` 디렉터리 존재 (상위 경로 `../gaia_dataset/`)
- `ANTHROPIC_API_KEY` 또는 `OPENAI_API_KEY` 환경 변수 (`qa`, `qa-gen`, `evaluate` 명령 시)
- `HUGGING_FACE_HUB_TOKEN` 환경 변수 (private/gated HuggingFace 모델 사용 시)
- NVIDIA GPU 사용 시: NVIDIA Container Toolkit 설치 필요

---

## Docker 실행 (기본)

`qdrant_rag/` 디렉터리 안의 `docker-compose.yml` 사용 — `gaia_ragas`와 독립 실행.

### 1. 사전 설정

```bash
cd qdrant_rag
cp .env.example .env
vi .env   # LLM_PROVIDER와 API key 입력, HOST_UID/HOST_GID / GPU 번호 확인

docker compose build
```

`.env` 주요 항목:

```
# Claude 기본값
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-6

# ChatGPT/OpenAI 사용 시
# LLM_PROVIDER=chatgpt
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-5-mini

HUGGING_FACE_HUB_TOKEN=        # private/gated 모델 사용 시 입력

# 임베딩 모델 (모델 변경 시 EMBED_DIMENSION도 반드시 함께 수정)
EMBED_MODEL=jhgan/ko-sroberta-multitask
EMBED_DIMENSION=768

# 하이브리드 검색 (BM25 + Dense RRF) — 변경 시 --reset 재인제스트 필요
USE_HYBRID_SEARCH=true
BM25_MODEL=Qdrant/bm25

# GPU 설정
EMBED_SERVER_GPU=0    # embed-server 전용 GPU
CUDA_VISIBLE_DEVICES=1  # ingest 단일 GPU (--gpus 사용 시 무시)

# 컨테이너 실행 사용자 (id -u && id -g 로 확인)
HOST_UID=1000
HOST_GID=1000
```

> **CUDA 버전 확인**: `nvidia-smi` 상단 `CUDA Version` 값에 맞게 Dockerfile의 `cu121`을 `cu118` / `cu124` 등으로 수정 후 재빌드.

### 2. 서비스 시작

```bash
# Qdrant DB + embed-server 시작 (상시 유지)
docker compose up -d qdrant embed-server

# embed-server 로그 확인 (모델 로딩 완료 확인)
docker compose logs embed-server

# embed-server 중지
docker compose stop embed-server
```

> `embed-server`는 `restart: unless-stopped` — 크래시/재부팅 시 자동 재기동.  
> `docker compose stop embed-server`로 명시적으로 중지하면 재부팅 후에도 멈춘 상태를 유지.

### 3. 인제스트

```bash
# 전체 인제스트 (177K 파일, 시간 소요)
docker compose run --rm qdrant-rag ./run.sh ingest

# 테스트용 소규모 (500파일)
docker compose run --rm qdrant-rag ./run.sh ingest --limit 500

# 특정 회사만
docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자

# GPU 번호 지정 (기본: .env의 CUDA_VISIBLE_DEVICES)
docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자 --gpu 7

# 파싱 병렬화 (CPU 코어 수에 맞게, 8~16 권장)
docker compose run --rm qdrant-rag ./run.sh ingest --workers 8 --gpu 7

# 멀티 GPU — GPU 수만큼 프로세스를 병렬로 띄워 파일 분담
docker compose run --rm qdrant-rag ./run.sh ingest --gpus 2,3,4,5
docker compose run --rm qdrant-rag ./run.sh ingest --gpus 2,3,4,5 --workers 8

# 컬렉션 + 체크포인트 완전 초기화 후 재인제스트
# USE_HYBRID_SEARCH 변경 시 반드시 --reset 필요 (컬렉션 스키마가 바뀜)
docker compose run --rm qdrant-rag ./run.sh ingest --reset
```

> `--reset`은 Qdrant 컬렉션을 삭제하고 `output/ingest_checkpoint*.json`을 모두 삭제합니다.
>
> **인제스트는 embed-server를 사용하지 않습니다.** 각 GPU 워커가 직접 모델을 로딩하여 병렬 처리합니다.

### 4. 백그라운드 실행 (세션 끊겨도 계속)

```bash
# 백그라운드로 인제스트 시작
docker compose run -d qdrant-rag ./run.sh ingest --gpus 4,5,6,7 --workers 8

# 진행 상황 실시간 확인
tail -f output/ingest.log

# 상태 요약 (체크포인트 + 로그 최근 10줄)
docker compose run --rm qdrant-rag ./run.sh status
```

### 5. 검색 / Q&A 테스트

```bash
# 벡터 검색 (쿼리 내 연도 자동 감지 → 날짜 필터 적용)
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"

# 날짜/회사 명시 필터
docker compose run --rm qdrant-rag ./run.sh search "영업이익" --company 삼성전자 --from 20210101 --to 20211231

# RAG Q&A
docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"
docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자

# 컬렉션 상태 확인
docker compose run --rm qdrant-rag ./run.sh info
```

> embed-server가 기동 중이면 모델 로딩 없이 즉시 응답합니다.  
> embed-server가 없으면 자동으로 로컬 모델 로딩으로 fallback합니다.

### 6. QA 쌍 생성

```bash
# 전체 100개 샘플 → output/qa_pairs.json
docker compose run --rm qdrant-rag ./run.sh qa-gen

# 특정 회사 → output/qa_pairs_삼성전자.json
docker compose run --rm qdrant-rag ./run.sh qa-gen --company 삼성전자

# 샘플 수 / 문서당 QA 수 조정
docker compose run --rm qdrant-rag ./run.sh qa-gen --sample 50 --qa-per-doc 3
```

### 7. RAGAS 평가

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
export EMBED_MODEL=jhgan/ko-sroberta-multitask
export EMBED_DIMENSION=768
export CUDA_VISIBLE_DEVICES=0
```

### 2. 실행 (명령어는 Docker와 동일)

```bash
./run.sh ingest --company 삼성전자
./run.sh ingest --workers 8 --gpu 2
./run.sh ingest --gpus 2,3,4,5 --workers 8
./run.sh ingest --reset
./run.sh status
./run.sh search "삼성전자 2021년 영업이익"
./run.sh search "영업이익" --company 삼성전자 --from 20210101 --to 20211231
./run.sh qa "매출액은?" --company 삼성전자
./run.sh qa-gen --company 삼성전자
./run.sh evaluate --company 삼성전자 --limit 20
./run.sh info
```

> Qdrant는 로컬 파일 모드(`qdrant_storage/`)로 자동 실행됩니다.  
> 20,000 벡터 초과 시 성능 경고 발생 — 대규모 테스트는 Docker 모드 권장.

---

## 설정 (config.py / 환경 변수)

| 변수 | 기본값 | 환경 변수 | 설명 |
|------|--------|-----------|------|
| `GAIA_DATASET_DIR` | `../gaia_dataset` | `GAIA_DATASET_DIR` | 인제스트 대상 데이터 경로 |
| `QDRANT_URL` | `None` (로컬 파일) | `QDRANT_URL` | Docker 시 `http://qdrant:6333` |
| `QDRANT_PATH` | `./qdrant_storage` | `QDRANT_PATH` | 로컬 파일 DB 경로 |
| `COLLECTION_NAME` | `dart_kospi200` | — | Qdrant 컬렉션 이름 |
| `EMBED_MODEL` | `jhgan/ko-sroberta-multitask` | `EMBED_MODEL` | 임베딩 모델 |
| `EMBED_DIMENSION` | `768` | `EMBED_DIMENSION` | 임베딩 벡터 차원 (모델 변경 시 함께 수정) |
| `EMBED_BATCH_SIZE` | `64` | `EMBED_BATCH_SIZE` | GPU당 배치 크기 (VRAM에 맞게 조정) |
| `HF_TOKEN` | `None` | `HUGGING_FACE_HUB_TOKEN` | HuggingFace private/gated 모델 접근 토큰 |
| `EMBED_SERVER_URL` | `None` | `EMBED_SERVER_URL` | embed-server 주소 (Docker: `http://embed-server:8765`) |
| `USE_HYBRID_SEARCH` | `true` | `USE_HYBRID_SEARCH` | BM25 + Dense 하이브리드 검색 (변경 시 `--reset` 필요) |
| `BM25_MODEL` | `Qdrant/bm25` | `BM25_MODEL` | BM25 sparse 임베딩 모델 |
| `CHUNK_SIZE` | `800` | `CHUNK_SIZE` | 청크 글자 수 (길수록 문맥↑, 짧을수록 정밀검색↑) |
| `CHUNK_OVERLAP` | `100` | `CHUNK_OVERLAP` | 청크 간 오버랩 글자 수 |
| `MIN_CHUNK_LENGTH` | `50` | `MIN_CHUNK_LENGTH` | 이 글자 수 미만 청크 제외 |
| `CHUNK_SEPARATORS` | `["\n\n","\n","。","."," ",""]` | `CHUNK_SEPARATORS` | 분리 우선순위 (JSON 배열) |
| `CHUNK_KEEP_SEPARATOR` | `false` | `CHUNK_KEEP_SEPARATOR` | 분리자를 청크에 포함 여부 |
| `CHUNK_SEPARATOR_REGEX` | `false` | `CHUNK_SEPARATOR_REGEX` | 분리자를 정규식으로 해석 여부 |
| `TOP_K` | `5` | `TOP_K` | 검색 반환 문서 수 |
| `LOG_INTERVAL` | `1000` | `LOG_INTERVAL` | `ingest.log` 기록 간격 (처리 파일 수 기준) |
| `LLM_PROVIDER` | `claude` | `LLM_PROVIDER` | `claude` 또는 `chatgpt` |
| `LLM_MODEL` | provider별 기본값 | `LLM_MODEL` | Q&A / 평가 LLM 모델 직접 지정 |
| `ANTHROPIC_API_KEY` | — | `ANTHROPIC_API_KEY` | Claude API 키 |
| `OPENAI_API_KEY` | — | `OPENAI_API_KEY` | ChatGPT/OpenAI API 키 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | `CLAUDE_MODEL` | Claude 기본 모델 |
| `OPENAI_MODEL` | `gpt-5-mini` | `OPENAI_MODEL` | ChatGPT/OpenAI 기본 모델 |
| `OUTPUT_DIR` | `./output` | `OUTPUT_DIR` | 결과 저장 디렉터리 |
| — | `1` | `CUDA_VISIBLE_DEVICES` | ingest 단일 GPU 번호 |
| — | `0` | `EMBED_SERVER_GPU` | embed-server GPU 번호 |

---

## 검색 동작 방식

### 하이브리드 검색 (USE_HYBRID_SEARCH=true)

Dense 벡터와 BM25 sparse 벡터를 **RRF(Reciprocal Rank Fusion)** 로 결합합니다.

- Dense: 의미적 유사도 (한국어 문맥 이해)
- BM25: 키워드 정밀도 (회사명, 수치, 고유명사)
- RRF fusion: 두 결과의 순위를 결합해 최종 순위 결정

> `USE_HYBRID_SEARCH` 값을 변경한 뒤에는 반드시 `--reset` 재인제스트 필요  
> (컬렉션 스키마가 `dense` 단일 벡터 ↔ `dense + bm25` 이중 벡터로 달라짐)

### 날짜 자동 감지

쿼리에 연도/분기 표현이 있으면 `filing_date` 필터를 자동 적용합니다.  
`filing_date`는 정수(`YYYYMMDD`)로 저장되며 Qdrant Range 필터로 범위 검색합니다.

| 쿼리 예시 | 적용 필터 |
|-----------|-----------|
| `삼성전자 2021년 영업이익` | `20210101 ~ 20211231` |
| `LG화학 2022년 1분기 매출` | `20220101 ~ 20220331` |
| `현대차 2020년 상반기 실적` | `20200101 ~ 20200630` |
| `SK하이닉스 2023년 하반기 투자` | `20230701 ~ 20231231` |

명시적 날짜 필터(`--from` / `--to`)가 있으면 자동 감지보다 우선 적용됩니다.

---

## RAGAS 평가 메트릭

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `faithfulness` | ≥ 0.70 | 답변이 검색 문서에 근거하는가 |
| `answer_relevancy` | ≥ 0.60 | 답변이 질문과 관련있는가 |
| `context_precision` | ≥ 0.50 | 검색된 문서가 관련있는가 |
| `context_recall` | ≥ 0.50 | 관련 문서를 충분히 검색했는가 |

평가 결과는 `output/qdrant_eval.csv`, `output/qdrant_eval.xlsx`, `output/qdrant_eval.json` 으로 저장됩니다.

### RAGAS 평가 동작 방식

- **LLM**: `LLM_PROVIDER` 설정을 따름 (Claude 또는 ChatGPT) — OpenAI를 기본값으로 사용하지 않음
- **Embeddings**: embed-server가 기동 중이면 embed-server 재활용, 없으면 로컬 HuggingFace 모델 사용 — OpenAI embedding fallback 없음
- **QA dataset 재사용**: `qa-gen`으로 생성한 `qa_pairs_{회사}.json`은 한 번 생성 후 반복 평가에 재사용 가능 (파라미터 튜닝 비교 시 동일 dataset으로 공정 비교)
- **신뢰성 있는 샘플 수**: 100개 이상 권장 (10개는 sanity check 수준, 50개는 초기 확인용)
- **RAGAS 버전**: 0.4.3 기준 (`Faithfulness()` 등 클래스 인스턴스 방식)

---

## 참고

- 임베딩 모델 최초 실행 시 HuggingFace 자동 다운로드
  - **Docker**: `~/.cache/huggingface/` (호스트) ↔ `/app/hf_cache` (컨테이너) 볼륨 마운트로 공유
  - **로컬**: `~/.cache/huggingface/hub/`
- **embed-server**: Dense + BM25 모델을 한 번 로딩 후 HTTP로 상시 서빙 — search/qa 콜드스타트 제거
  - `EMBED_SERVER_GPU` — embed-server 전용 GPU (인제스트 GPU와 분리 권장)
  - embed-server 미기동 시 retriever가 로컬 모델로 자동 fallback
- **ingest**는 embed-server를 사용하지 않음 — 각 GPU 워커가 직접 로딩하여 병렬 처리
- `--gpu N` : 단일 GPU 지정 (기본: `.env`의 `CUDA_VISIBLE_DEVICES`)
- `--gpus N,N,...` : 멀티 GPU — GPU당 별도 프로세스를 spawn하여 파일 균등 분배 (CUDA fork 이슈 없음)
- `--workers N` : 파일 파싱 병렬 스레드 수 (CPU 코어 수에 맞게, 8~16 권장)
- 멀티 GPU 인제스트는 GPU당 `output/ingest_checkpoint_gpu{N}.json`에 개별 저장
- `output/ingest.log` — 인제스트 진행 로그; 백그라운드 실행 시 `tail -f`로 실시간 확인 가능
- `./run.sh status` — 체크포인트 파일 집계 + 로그 최근 10줄 요약
- 인제스트 중단 시 체크포인트 기반으로 이어서 재개 가능
- `--reset` 실행 시 Qdrant 컬렉션과 체크포인트 파일 전체(`ingest_checkpoint*.json`) 삭제
- QA 생성 중단 시 `output/qa_pairs.json` 기반으로 이어서 재개 가능 (doc_id 기준)
- 컨테이너는 호스트 사용자(`HOST_UID`/`HOST_GID`)로 실행되어 볼륨 마운트 파일 권한 문제가 없음
- `gaia_ragas`와는 `gaia_dataset/` 만 공유하며 완전 독립 실행 가능

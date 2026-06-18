# gaia_ragas — Cohesity GAIA 평가 파이프라인

DART KOSPI200 한국 금융 공시 문서에서 QA 쌍을 생성하고
Cohesity GAIA API 및 RAGAS로 RAG 품질을 평가하는 파이프라인.

## 현재 테스트 데이터 범위

- `gaia_dataset/` 파일명 기준 날짜 범위: **2015년 ~ 2026년**
- qdrant RAG 테스트 컬렉션(`dart_kospi200`) 기준 `filing_date` 범위: **2015-02-13 ~ 2026-05-08**
- 연간 재무 질의(`영업이익`, `매출`, `순이익`, `사업보고서`, `감사보고서` 등)는 사업연도 기준으로 해석해야 합니다.
  예: `삼성전자 2015년 영업이익`은 2015년 사업연도 질문이며, 2016년 제출 감사보고서/사업보고서에 답이 있을 수 있습니다.

## 파일 구조

```
gaia_ragas/
├── config.py               설정 (경로, LLM provider/model, 샘플링 파라미터)
├── document_sampler.py     gaia_dataset 에서 문서 샘플링
├── qa_generator.py         LLM API 로 QA 쌍 생성
├── ragas_testset_creator.py QA 쌍 → RAGAS 테스트셋 변환
├── gaia_evaluator.py       Cohesity GAIA API 평가 + RAGAS 메트릭 계산
├── eval_pipeline.py        교보증권/삼성전자/현대자동차 혼합 200개 평가 전용 스크립트
├── run_pipeline.py         단계별 파이프라인 진입점
├── dart_xml_parser.py      XML/PDF/XLS 파싱
├── consolidate_docs.py     dataset1/2/3 → gaia_dataset 통합 유틸
├── requirements.txt        Python 패키지 목록
├── Dockerfile              컨테이너 이미지 정의
├── docker-compose.yml      컨테이너 구성
├── .env.example            환경 변수 템플릿
├── run.sh                  로컬 실행 스크립트 (.venv 자동 감지)
└── eval/                   혼합 평가 결과 전용 디렉터리
    ├── qa_pairs_mixed.json          3개사 혼합 QA 쌍 (200개) — 재사용 가능
    ├── ragas_testset.json           RAGAS 평가용 테스트셋
    ├── gaia_eval.csv                GAIA 평가용 CSV
    ├── gaia_eval_results.csv        GAIA API 응답 결과
    └── ragas_eval_results.csv       RAGAS 4개 메트릭 점수
```

## 처리 흐름

```
gaia_dataset/ (XML/PDF/XLS)
      │
      ▼
document_sampler.py  →  랜덤 샘플링
      │
      ▼
qa_generator.py      →  LLM API 로 QA 쌍 생성 (문서당 2개)  ← 최초 1회만 생성, 재사용 가능
      │
      ▼
ragas_testset_creator.py  →  RAGAS SingleTurnSample 형식 변환
      │
      ▼
gaia_evaluator.py    →  Cohesity GAIA API 쿼리 (POST /gaia/ask) + RAGAS 메트릭 계산
```

### Cohesity GAIA API

- 엔드포인트: `POST /gaia/ask`
- 인증: `apiKey` 헤더 (Bearer 토큰 아님)
- 페이로드: `{"queryString": "질문", "datasetNames": ["데이터셋명"]}`
- 임베딩/리랭킹/LLM 추론은 Gaia 내부에서 자동 처리 (NVIDIA NeMo Retriever)

---

## Docker 실행 (권장)

Docker 이미지에 모든 의존성이 포함되어 있어 **다른 머신에서도 Docker만 있으면 바로 실행 가능**합니다.
`docker-compose.yml`과 같은 디렉터리의 `.env` 파일은 Docker Compose가 자동으로 로드합니다.

### 1. 빌드 머신 (최초 1회)

```bash
cd gaia_ragas
cp .env.example .env
vi .env   # API 키 입력 (아래 항목 참고)

docker compose build
```

레지스트리에 푸시해 다른 머신에서 사용할 경우:

```bash
docker tag cohesity-gaia-ragas <registry>/gaia-ragas:latest
docker push <registry>/gaia-ragas:latest
```

### 2. 테스트 머신 (Docker만 있으면 됨)

```bash
# docker-compose.yml + .env 파일만 준비
cp .env.example .env
vi .env

# 이미지 pull (레지스트리 사용 시)
docker pull <registry>/gaia-ragas:latest
```

`.env` 항목:

```ini
# LLM — QA 생성 및 RAGAS 평가에 사용
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-6

# ChatGPT/OpenAI 사용 시
# LLM_PROVIDER=chatgpt
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-5-mini

# Cohesity GAIA API — evaluate 단계에서 필요
COHESITY_CLUSTER_URL=https://<helios-fqdn>
COHESITY_API_KEY=<api-key>          # Settings > Access Management > API Keys
COHESITY_DATASET_NAME=<dataset-name>

# 컨테이너 실행 사용자 (볼륨 파일 권한을 호스트 사용자와 일치)
# 값 확인: id -u && id -g
HOST_UID=1000
HOST_GID=1000
```

---

## 혼합 평가 파이프라인 (eval_pipeline.py)

교보증권 / 삼성전자 / 현대자동차 3개사에서 균등하게 QA를 생성해
200개 혼합 데이터셋을 만들고 GAIA/RAGAS 평가를 수행합니다.

회사별 QA 수: 교보증권 68개 / 삼성전자 67개 / 현대자동차 67개 = **200개**

결과는 `eval/` 볼륨에 저장되어 호스트에서 직접 확인 가능합니다.

```bash
# Step 1: QA 200개 생성 (최초 1회 — 이후 재사용)
docker compose run --rm gaia-ragas python eval_pipeline.py --step qa

# Step 2: RAGAS 테스트셋 변환
docker compose run --rm gaia-ragas python eval_pipeline.py --step testset

# Step 3: GAIA API 호출 + RAGAS 메트릭 평가 (COHESITY_* 필요)
docker compose run --rm gaia-ragas python eval_pipeline.py --step evaluate

# 전체 한 번에
docker compose run --rm gaia-ragas python eval_pipeline.py --step all

# eval/ 초기화 후 재실행
docker compose run --rm gaia-ragas python eval_pipeline.py --step qa --reset
```

---

## 일반 파이프라인 (run_pipeline.py)

단일 회사 또는 전체 랜덤 샘플링으로 실행하는 기존 파이프라인.

```bash
# 전체 파이프라인 (샘플링 → QA 생성 → 테스트셋 변환)
docker compose run --rm gaia-ragas ./run.sh

# 단계별 실행
docker compose run --rm gaia-ragas ./run.sh --step sample    # 문서 샘플링
docker compose run --rm gaia-ragas ./run.sh --step qa        # QA 생성
docker compose run --rm gaia-ragas ./run.sh --step testset   # RAGAS 테스트셋 변환
docker compose run --rm gaia-ragas ./run.sh --step evaluate  # GAIA API 평가

# 특정 회사만
docker compose run --rm gaia-ragas ./run.sh --step qa --company 삼성전자

# 초기화 후 재실행
docker compose run --rm gaia-ragas ./run.sh --reset

# 문서 통합 (dataset1/2/3 → gaia_dataset, 최초 1회)
docker compose run --rm gaia-ragas ./run.sh consolidate
```

---

## 설정 (config.py)

| 변수 | 기본값 | 환경 변수 | 설명 |
|------|--------|-----------|------|
| `GAIA_DATASET_DIR` | `../gaia_dataset` | `GAIA_DATASET_DIR` | 샘플링 대상 데이터 경로 |
| `OUTPUT_DIR` | `./output` | `OUTPUT_DIR` | 일반 파이프라인 결과 저장 경로 |
| `LLM_PROVIDER` | `claude` | `LLM_PROVIDER` | `claude` 또는 `chatgpt` |
| `LLM_MODEL` | provider별 기본값 | `LLM_MODEL` | QA 생성 / 평가 LLM 모델 직접 지정 |
| `ANTHROPIC_API_KEY` | — | `ANTHROPIC_API_KEY` | Claude API 키 |
| `OPENAI_API_KEY` | — | `OPENAI_API_KEY` | ChatGPT/OpenAI API 키 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | `CLAUDE_MODEL` | Claude 기본 모델 |
| `OPENAI_MODEL` | `gpt-5-mini` | `OPENAI_MODEL` | ChatGPT/OpenAI 기본 모델 |
| `OPENAI_MAX_OUTPUT_TOKENS` | `4096` | `OPENAI_MAX_OUTPUT_TOKENS` | OpenAI 응답 출력 토큰 상한 |
| `OPENAI_REASONING_EFFORT` | `minimal` | `OPENAI_REASONING_EFFORT` | GPT-5/o 계열 reasoning effort |
| `SAMPLE_SIZE` | `100` | — | 샘플링 문서 수 |
| `QA_PER_DOC` | `2` | — | 문서당 QA 쌍 수 |
| `MIN_TEXT_LENGTH` | `300` | — | 최소 텍스트 길이 (미만 제외) |
| `MAX_TEXT_LENGTH` | `8000` | — | LLM 입력 최대 텍스트 길이 |

---

## 결과 파일

### 일반 파이프라인 (`output/`)

| 파일 | 생성 단계 | 설명 |
|------|-----------|------|
| `output/sampled_documents.json` | `--step sample` | 전체 샘플링 문서 |
| `output/sampled_documents_{회사}.json` | `--step sample --company` | 회사별 샘플링 문서 |
| `output/qa_pairs.json` | `--step qa` | 전체 QA 쌍 (**재사용 가능**) |
| `output/qa_pairs_{회사}.json` | `--step qa --company` | 회사별 QA 쌍 (재사용 가능) |
| `output/ragas_testset.json` | `--step testset` | RAGAS 평가용 테스트셋 |
| `output/gaia_eval_results.csv` | `--step evaluate` | GAIA API 평가 결과 |
| `output/ragas_eval_results.csv` | `--step evaluate` | RAGAS 4개 메트릭 점수 |

### 혼합 평가 파이프라인 (`eval/`)

| 파일 | 생성 단계 | 설명 |
|------|-----------|------|
| `eval/qa_pairs_mixed.json` | `--step qa` | 3개사 혼합 QA 쌍 200개 (**재사용 가능**) |
| `eval/ragas_testset.json` | `--step testset` | RAGAS 평가용 테스트셋 |
| `eval/gaia_eval.csv` | `--step testset` | GAIA 평가용 CSV |
| `eval/gaia_eval_results.csv` | `--step evaluate` | GAIA API 응답 결과 |
| `eval/ragas_eval_results.csv` | `--step evaluate` | RAGAS 4개 메트릭 점수 |

---

## 참고

- QA 데이터셋(`qa_pairs_mixed.json`)은 최초 1회 생성 후 재사용 — LLM 호출 비용 절약
- RAGAS 평가 시 OpenAI embedding fallback 방지를 위해 `paraphrase-multilingual-MiniLM-L12-v2` 사용
- `COHESITY_*` 환경변수 미설정 시 GAIA 평가는 자동으로 skip되고 RAGAS만 실행
- `qdrant_rag`와는 `gaia_dataset/` 만 공유하며 독립적으로 실행 가능
- 컨테이너는 호스트 사용자(`HOST_UID`/`HOST_GID`)로 실행되어 볼륨 파일 권한 문제 없음

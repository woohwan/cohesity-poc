# gaia_ragas — Cohesity GAIA 평가 파이프라인

DART KOSPI200 한국 금융 공시 문서에서 QA 쌍을 생성하고
Cohesity GAIA API 및 RAGAS로 RAG 품질을 평가하는 파이프라인.

## 파일 구조

```
gaia_ragas/
├── config.py               설정 (경로, Claude 모델, 샘플링 파라미터)
├── document_sampler.py     gaia_dataset 에서 문서 샘플링
├── qa_generator.py         Claude API 로 QA 쌍 생성
├── ragas_testset_creator.py QA 쌍 → RAGAS 테스트셋 변환
├── gaia_evaluator.py       Cohesity GAIA API 평가 + RAGAS 메트릭 계산
├── run_pipeline.py         단계별 파이프라인 진입점
├── dart_xml_parser.py      XML/PDF/XLS 파싱 (qdrant_rag 컨테이너와 공유)
├── consolidate_docs.py     dataset1/2/3 → gaia_dataset 통합 유틸
├── requirements.txt        Python 패키지 목록
├── Dockerfile              컨테이너 이미지 정의
├── docker-compose.yml      컨테이너 구성 (gaia_ragas 단독 실행)
├── .env.example            환경 변수 템플릿
└── run.sh                  컨테이너 / 로컬 통합 실행 스크립트
```

## 처리 흐름

```
gaia_dataset/ (XML/PDF/XLS)
      │
      ▼
document_sampler.py  →  랜덤 샘플링 (기본 100개)
      │
      ▼
qa_generator.py      →  Claude API 로 QA 쌍 생성 (문서당 2개)
      │
      ▼
ragas_testset_creator.py  →  RAGAS SingleTurnSample 형식 변환
      │
      ▼
gaia_evaluator.py    →  Cohesity GAIA API 쿼리 + RAGAS 메트릭 계산
```

## 사전 조건

- `gaia_dataset/` 디렉터리 존재 (상위 경로 `../gaia_dataset/`)
- `ANTHROPIC_API_KEY` 환경 변수 (QA 생성 및 RAGAS 평가 시)
- GAIA 평가 시 추가 환경 변수:
  - `COHESITY_CLUSTER_URL` — `https://<cluster-ip>`
  - `COHESITY_API_TOKEN` — Bearer 토큰
  - `COHESITY_COLLECTION_ID` — GAIA 컬렉션 ID

---

## Docker 실행 (기본)

`gaia_ragas/` 디렉터리 안의 `docker-compose.yml` 사용 — `qdrant_rag`와 독립 실행.

### 1. 사전 설정

```bash
cd gaia_ragas
cp .env.example .env
vi .env   # ANTHROPIC_API_KEY 입력, UID/GID 확인 (id -u && id -g)

docker compose build
```

`.env` 주요 항목:

```
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-6    # 기본값, 변경 가능

# 컨테이너 실행 사용자 (호스트 사용자와 일치시켜 볼륨 권한 문제 방지)
UID=1000
GID=1000
```

### 2. 전체 파이프라인 (A안 — 랜덤 샘플링)

```bash
# 샘플링 → QA 생성 → 테스트셋 변환 한 번에
docker compose run --rm gaia-ragas ./run.sh

# 단계별 실행
docker compose run --rm gaia-ragas ./run.sh --step sample    # 문서 샘플링
docker compose run --rm gaia-ragas ./run.sh --step qa        # QA 생성
docker compose run --rm gaia-ragas ./run.sh --step testset   # RAGAS 테스트셋 변환
docker compose run --rm gaia-ragas ./run.sh --step evaluate  # GAIA API 평가 (클러스터 필요)
```

### 3. 특정 회사 (B안)

```bash
docker compose run --rm gaia-ragas ./run.sh --step sample --company 삼성전자
docker compose run --rm gaia-ragas ./run.sh --step qa     --company 삼성전자
```

### 4. 초기화 후 재실행

```bash
# 기존 출력 파일 전체 삭제 후 전체 파이프라인 재실행
docker compose run --rm gaia-ragas ./run.sh --reset

# 특정 회사 초기화 후 재실행
docker compose run --rm gaia-ragas ./run.sh --reset --company 삼성전자
```

> `--reset`은 `output/` 안의 sampled_documents, qa_pairs, ragas_testset, gaia_eval_results 파일을 삭제합니다.

### 5. 문서 통합 유틸 (최초 1회 — dataset1/2/3 → gaia_dataset)

```bash
docker compose run --rm gaia-ragas ./run.sh consolidate
docker compose run --rm gaia-ragas ./run.sh consolidate --dry-run    # 미리보기
docker compose run --rm gaia-ragas ./run.sh consolidate --workers 8  # 병렬 처리
```

---

## 로컬 실행 (선택)

Docker 없이 로컬 `.venv`에서 직접 실행하는 방법.

### 1. 환경 설정

```bash
cd gaia_ragas
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

### 2. 실행 (명령어는 Docker와 동일)

```bash
./run.sh                                    # 전체 파이프라인
./run.sh --step sample --company 삼성전자   # 특정 회사 샘플링
./run.sh --step qa     --company 삼성전자   # 특정 회사 QA 생성
./run.sh --reset                            # 출력 파일 초기화 후 재실행
./run.sh consolidate                        # 문서 통합
```

---

## 설정 (config.py)

| 변수 | 기본값 | 환경 변수 | 설명 |
|------|--------|-----------|------|
| `GAIA_DATASET_DIR` | `../gaia_dataset` | `GAIA_DATASET_DIR` | 샘플링 대상 데이터 경로 |
| `OUTPUT_DIR` | `./output` | `OUTPUT_DIR` | 결과 저장 디렉터리 |
| `ANTHROPIC_API_KEY` | — | `ANTHROPIC_API_KEY` | Claude API 키 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | `CLAUDE_MODEL` | QA 생성 / 평가 LLM |
| `SAMPLE_SIZE` | `100` | — | 샘플링 문서 수 |
| `QA_PER_DOC` | `2` | — | 문서당 QA 쌍 수 |
| `MIN_TEXT_LENGTH` | `300` | — | 최소 텍스트 길이 (미만 제외) |
| `MAX_TEXT_LENGTH` | `8000` | — | Claude 입력 최대 텍스트 길이 |

---

## 결과 파일

| 파일 | 생성 단계 | 설명 |
|------|-----------|------|
| `output/sampled_documents.json` | `--step sample` | 전체 샘플링 문서 |
| `output/sampled_documents_{회사}.json` | `--step sample --company` | 회사별 샘플링 문서 |
| `output/qa_pairs.json` | `--step qa` | 전체 QA 쌍 |
| `output/qa_pairs_{회사}.json` | `--step qa --company` | 회사별 QA 쌍 |
| `output/ragas_testset.json` | `--step testset` | RAGAS 평가용 테스트셋 |
| `output/gaia_eval_results.csv` | `--step evaluate` | GAIA API 평가 결과 |

---

## 참고

- QA 생성 중단 시 `output/qa_pairs.json` 기반으로 이어서 재개 가능 (doc_id 중복 제외)
- `--reset` 실행 시 위 출력 파일 전체 삭제 후 처음부터 재실행
- `qdrant_rag`와는 `gaia_dataset/` 만 공유하며 독립적으로 실행 가능
- `dart_xml_parser.py`는 `qdrant_rag` 컨테이너 빌드 시에도 복사되어 재사용됨
- 컨테이너는 호스트 사용자(`UID`/`GID`)로 실행되어 볼륨 마운트 파일 권한 문제가 없음

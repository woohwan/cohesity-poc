# Cohesity GAIA RAG 평가 파이프라인

DART KOSPI200 한국 금융 공시 문서를 활용한 Cohesity GAIA RAG 품질 평가 프레임워크.
`gaia_ragas`와 `qdrant_rag`는 `gaia_dataset/`만 공유하며 각각 독립적으로 실행됩니다.

## 디렉터리 구조

```
cohesity-poc/
├── .dockerignore               ← Docker 빌드 컨텍스트 제외 목록
├── .env.example                ← 루트 통합 실행용 환경 변수 템플릿
├── docker-compose.yml          ← 루트: 두 서비스 통합 실행용 (선택)
├── gaia_dataset/               ← 공유 데이터 (XML/PDF/XLS)
├── gaia_ragas/                 ← Cohesity GAIA 평가 파이프라인
│   ├── docker-compose.yml      ← 단독 실행용
│   ├── .env.example
│   └── output/                 ← 샘플링/QA/테스트셋 결과 (자동 생성)
├── qdrant_rag/                 ← Qdrant RAG 평가 파이프라인
│   ├── docker-compose.yml      ← 단독 실행용 (Qdrant 포함)
│   ├── .env.example
│   ├── qdrant_storage/         ← Qdrant 벡터 DB (자동 생성)
│   └── output/                 ← 인제스트 체크포인트/QA/평가 결과 (자동 생성)
└── README.md
```

---

## gaia-ragas 단독 실행

```bash
cd gaia_ragas
cp .env.example .env
vi .env              # ANTHROPIC_API_KEY=sk-ant-... 입력
docker compose build

# 전체 파이프라인 (샘플링 → QA 생성 → 테스트셋 변환)
docker compose run --rm gaia-ragas ./run.sh

# 단계별
docker compose run --rm gaia-ragas ./run.sh --step sample
docker compose run --rm gaia-ragas ./run.sh --step qa
docker compose run --rm gaia-ragas ./run.sh --step testset
docker compose run --rm gaia-ragas ./run.sh --step evaluate  # GAIA 클러스터 필요

# 특정 회사 (B안)
docker compose run --rm gaia-ragas ./run.sh --step sample --company 삼성전자
docker compose run --rm gaia-ragas ./run.sh --step qa     --company 삼성전자
```

---

## qdrant-rag 단독 실행

```bash
cd qdrant_rag
cp .env.example .env
vi .env              # ANTHROPIC_API_KEY, HUGGING_FACE_HUB_TOKEN 입력
docker compose build
docker compose up -d qdrant   # Qdrant DB 시작

# A안 — 전체 랜덤 테스트
docker compose run --rm qdrant-rag ./run.sh ingest
docker compose run --rm qdrant-rag ./run.sh qa-gen
docker compose run --rm qdrant-rag ./run.sh evaluate

# B안 — 특정 회사 테스트 (권장)
docker compose run --rm qdrant-rag ./run.sh ingest   --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh qa-gen   --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20

# 검색 / Q&A
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"
docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh info
```

---

## 통합 실행 (선택)

루트의 `docker-compose.yml`으로 두 서비스를 함께 실행할 수도 있습니다.

```bash
cd cohesity-poc
cp .env.example .env
vi .env
docker compose build
docker compose up -d qdrant
docker compose run --rm gaia-ragas ./run.sh --step sample --company 삼성전자
docker compose run --rm gaia-ragas ./run.sh --step qa     --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh ingest        --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh evaluate      --company 삼성전자 --limit 20
```

---

## 결과 파일 위치

| 파일 | 설명 |
|------|------|
| `gaia_ragas/output/qa_pairs.json` | 전체 QA 쌍 |
| `gaia_ragas/output/qa_pairs_{회사}.json` | 회사별 QA 쌍 |
| `gaia_ragas/output/gaia_eval_results.csv` | GAIA API 평가 결과 |
| `qdrant_rag/output/qa_pairs.json` | qdrant_rag 자체 QA 쌍 |
| `qdrant_rag/output/qdrant_eval.csv` | RAGAS 평가 결과 |
| `qdrant_rag/output/qdrant_eval.json` | RAGAS 점수 요약 |

---

## RAGAS 평가 목표 기준

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `faithfulness` | ≥ 0.70 | 답변이 검색 문서에 근거하는가 |
| `answer_relevancy` | ≥ 0.60 | 답변이 질문과 관련있는가 |
| `context_precision` | ≥ 0.50 | 검색된 문서가 관련있는가 |
| `context_recall` | ≥ 0.50 | 관련 문서를 충분히 검색했는가 |

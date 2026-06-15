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
│   └── output/                 ← 인제스트 체크포인트/로그/QA/평가 결과 (자동 생성)
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
vi .env              # ANTHROPIC_API_KEY, HOST_UID/HOST_GID 입력
docker compose build
docker compose up -d qdrant embed-server   # Qdrant DB + 임베딩 서버 시작
docker compose logs embed-server           # 모델 로딩 완료 확인

# 소규모 테스트 (권장 순서)
docker compose run --rm qdrant-rag ./run.sh ingest   --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh qa-gen   --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20

# 속도 향상 옵션
docker compose run --rm qdrant-rag ./run.sh ingest --workers 8 --gpu 7      # 파싱 병렬 + GPU 지정
docker compose run --rm qdrant-rag ./run.sh ingest --gpus 2,3,4,5 --workers 8  # 멀티 GPU

# 백그라운드 실행 (세션 끊겨도 계속)
docker compose run -d qdrant-rag ./run.sh ingest --gpus 4,5,6,7 --workers 8
tail -f qdrant_rag/output/ingest.log                                          # 진행 로그
docker compose run --rm qdrant-rag ./run.sh status                           # 상태 요약

# 검색 (쿼리 내 연도 자동 감지 → 공시일 필터 적용)
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"
docker compose run --rm qdrant-rag ./run.sh search "영업이익" --company 삼성전자 --from 20210101 --to 20211231

# RAG Q&A
docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh info
```

> **하이브리드 검색**: `USE_HYBRID_SEARCH=true`(기본값)일 때 Dense + BM25 RRF 방식으로 검색합니다.  
> 값을 변경한 뒤에는 반드시 `--reset` 재인제스트가 필요합니다.

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
| `qdrant_rag/output/ingest.log` | 인제스트 진행 로그 |
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

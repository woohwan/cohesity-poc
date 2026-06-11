# Cohesity GAIA RAG 평가 파이프라인

DART KOSPI200 한국 금융 공시 문서를 활용한 Cohesity GAIA RAG 품질 평가 프레임워크

## 사전 조건

- Docker Engine 및 Docker Compose 설치
- `gaia_dataset/` 디렉터리 존재 (dataset1/2/3 통합본, ~111GB)
- Anthropic API 키

## 디렉터리 구조

```
cohesity-poc/
├── docker-compose.yml
├── .env                        ← API 키 설정
├── gaia_dataset/               ← 111GB 통합 데이터 (XML/PDF/XLS)
├── gaia_ragas/                 ← QA 생성 / RAGAS 평가
│   └── output/                 ← qa_pairs.json 저장 (자동 생성)
├── qdrant_rag/
│   ├── qdrant_storage/         ← Qdrant 벡터 DB (자동 생성)
│   └── output/                 ← 평가 결과 저장 (자동 생성)
└── README.md
```

---

## 1. 최초 설정

```bash
# API 키 설정
cp .env.example .env
vi .env   # ANTHROPIC_API_KEY=sk-ant-... 입력

# 컨테이너 이미지 빌드 (최초 1회)
docker compose build
```

---

## 2. 컨테이너 시작

```bash
# Qdrant DB 백그라운드 시작
docker compose up -d qdrant

# 상태 확인
docker compose logs qdrant
```

---

## 3. A안 — 전체 랜덤 샘플링 테스트

모든 회사에서 랜덤 100개 샘플링 후 전체 인제스트와 매칭

```bash
# Step 1: 문서 샘플링 (gaia_dataset 에서 랜덤 100개)
docker compose run --rm gaia-ragas ./run.sh --step sample

# Step 2: QA 생성 (Claude API 사용)
docker compose run --rm gaia-ragas ./run.sh --step qa

# Step 3: 전체 문서 인제스트 (시간 소요 — 177K 파일)
docker compose run --rm qdrant-rag ./run.sh ingest

# Step 4: RAGAS 평가
docker compose run --rm qdrant-rag ./run.sh evaluate
```

---

## 4. B안 — 특정 회사 테스트 (권장)

인제스트와 QA를 같은 회사로 맞춰 정확한 평가 가능

```bash
# Step 1: 특정 회사 문서 샘플링
docker compose run --rm gaia-ragas ./run.sh --step sample --company 삼성전자

# Step 2: 특정 회사 QA 생성 → qa_pairs_삼성전자.json 생성
docker compose run --rm gaia-ragas ./run.sh --step qa --company 삼성전자

# Step 3: 특정 회사 인제스트
docker compose run --rm qdrant-rag ./run.sh ingest --company 삼성전자

# Step 4: RAGAS 평가 (20개 제한) → qa_pairs_삼성전자.json 자동 사용
docker compose run --rm qdrant-rag ./run.sh evaluate --company 삼성전자 --limit 20
```

---

## 5. 검색 / Q&A 단독 테스트

```bash
# 벡터 검색 테스트
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"

# RAG Q&A
docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"

# 특정 회사 대상 Q&A
docker compose run --rm qdrant-rag ./run.sh qa "매출액은?" --company 삼성전자

# Qdrant 컬렉션 상태 확인
docker compose run --rm qdrant-rag ./run.sh info
```

---

## 6. 컨테이너 관리

```bash
# 전체 컨테이너 중지
docker compose down

# 컬렉션 초기화 후 재인제스트
docker compose run --rm qdrant-rag ./run.sh ingest --reset

# 로그 확인
docker compose logs -f gaia-ragas
docker compose logs -f qdrant-rag

# 컨테이너 내부 접속 (디버깅)
docker compose run --rm gaia-ragas bash
docker compose run --rm qdrant-rag bash
```

---

## 7. 결과 파일 위치 (호스트)

| 파일 | 설명 |
|------|------|
| `gaia_ragas/output/sampled_documents.json` | 전체 랜덤 샘플링 문서 |
| `gaia_ragas/output/sampled_documents_{회사}.json` | 회사별 샘플링 문서 |
| `gaia_ragas/output/qa_pairs.json` | 전체 QA 쌍 |
| `gaia_ragas/output/qa_pairs_{회사}.json` | 회사별 QA 쌍 |
| `qdrant_rag/output/qdrant_eval.csv` | RAGAS 평가 결과 |
| `qdrant_rag/output/qdrant_eval.json` | RAGAS 점수 요약 |

---

## 8. RAGAS 평가 목표 기준

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| faithfulness | ≥ 0.70 | 답변이 검색 문서에 근거하는가 |
| answer_relevancy | ≥ 0.60 | 답변이 질문과 관련있는가 |
| context_precision | ≥ 0.50 | 검색된 문서가 관련있는가 |
| context_recall | ≥ 0.50 | 관련 문서를 충분히 검색했는가 |

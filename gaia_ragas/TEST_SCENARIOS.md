# DART KOSPI200 공시 문서 — Cohesity GAIA / RAGAS 테스트 시나리오

| 항목 | 내용 |
|------|------|
| 작성일 | 2026-06-11 |
| 대상 시스템 | Cohesity GAIA (RAG 기반 엔터프라이즈 AI 검색) |
| 평가 프레임워크 | RAGAS (Retrieval-Augmented Generation Assessment) |
| 데이터 범위 | KOSPI200 기업 271개사, 2015~2026년 공시 (총 177,353 파일) |
| 문서 유형 | XML 전자공시 / PDF 원문 / XLS 재무제표 |

---

## 목차

- [TC-P  파서 단위 테스트](#tc-p--파서-단위-테스트)
- [TC-S  샘플러 단위 테스트](#tc-s--샘플러-단위-테스트)
- [TC-Q  QA 생성 테스트](#tc-q--qa-생성-테스트)
- [TC-R  RAGAS 포맷 테스트](#tc-r--ragas-포맷-테스트)
- [TC-G  GAIA 쿼리 시나리오](#tc-g--gaia-쿼리-시나리오)
- [TC-E  엣지 케이스](#tc-e--엣지-케이스)
- [TC-I  통합 파이프라인 테스트](#tc-i--통합-파이프라인-테스트)
- [합격 기준 요약](#합격-기준-요약)
- [실행 명령어 요약](#실행-명령어-요약)

---

## TC-P  파서 단위 테스트

### TC-P-01  XML 파서 — 정상 공시 문서

| | |
|-|-|
| **입력** | `gaia_dataset/010130_고려아연/고려아연_20210831_공정위공시_000496.xml` |
| **기대 결과** | PASS |

**검증 항목**
- [ ] 텍스트 추출 성공 (`None` 아님)
- [ ] 길이 >= 300자
- [ ] 한글 포함
- [ ] `source_type == 'xml'`
- [ ] `company='고려아연'`, `filing_date='20210831'`

---

### TC-P-02  XML 파서 — 비정규 엔티티(`&cr;`) 처리

| | |
|-|-|
| **입력** | `&cr;` 포함 DART XML |
| **기대 결과** | PASS |

**검증 항목**
- [ ] `ParseError` 없이 정상 파싱
- [ ] 결과 텍스트에 `&cr;` 잔여 없음
- [ ] 의미있는 한글 텍스트 포함

---

### TC-P-03  PDF 파서 — 일반 공시 PDF

| | |
|-|-|
| **입력** | `[고려아연]대규모기업집단현황공시...(2021.08.31).pdf` |
| **기대 결과** | PASS |

**검증 항목**
- [ ] 텍스트 추출 성공
- [ ] 목차 또는 본문 내용 포함
- [ ] 길이 >= 300자
- [ ] `source_type == 'pdf'`

---

### TC-P-04  PDF 파서 — XML 없는 PDF-only 공시

| | |
|-|-|
| **입력** | `gaia_dataset/023530_롯데쇼핑/롯데쇼핑_20211115_주요사항보고서_002285.pdf` |
| **기대 결과** | PASS |

**검증 항목**
- [ ] XML 없는 폴더에서 PDF fallback 정상 동작
- [ ] 텍스트 추출 성공
- [ ] `source_type == 'pdf'`

---

### TC-P-05  XLS 파서 — 재무제표 변환

| | |
|-|-|
| **입력** | `gaia_dataset/010130_고려아연/고려아연_20150331_사업보고서_004325.xls` |
| **기대 결과** | PASS |

**검증 항목**
- [ ] 9개 시트 중 7개 추출 (기본정보 / 재무상태표 계열 / 손익계산서 계열 / 현금흐름표)
- [ ] 수치가 억원 단위로 변환되어 포함
- [ ] `=== 연결 재무상태표 ===` 섹션 포함
- [ ] `=== 연결 포괄손익계산서 ===` 섹션 포함
- [ ] `source_type == 'xls'`

---

### TC-P-06  메타데이터 추출 — 파일 경로 파싱

| | |
|-|-|
| **입력** | `gaia_dataset/010130_고려아연/고려아연_20210831_공정위공시_000496.xml` |
| **기대 결과** | PASS |

**검증 항목**

| 필드 | 기대값 |
|------|--------|
| `company_code` | `010130` |
| `company` | `고려아연` |
| `report_type` | `J` (공정위공시 역매핑) |
| `filing_date` | `20210831` |
| `receipt_no` | `000496` |
| `dataset` | `gaia_dataset` |

---

### TC-P-07  파서 우선순위 — XML+PDF+XLS 공존 폴더

| | |
|-|-|
| **입력** | 사업보고서 폴더 (XML + PDF + XLS 모두 존재) |
| **기대 결과** | PASS |

**검증 항목**
- [ ] `parse_filing_dir()` → `source_type == 'xml'` (XML 우선)
- [ ] `parse_file(pdf_path)` → `source_type == 'pdf'`
- [ ] `parse_file(xls_path)` → `source_type == 'xls'`

---

## TC-S  샘플러 단위 테스트

### TC-S-01  gaia_dataset 단일 디렉터리 샘플링

| 설정 | 값 |
|------|----|
| `sample_size` | 100 |
| `seed` | 42 |
| 소스 | `gaia_dataset/` (dataset1/2/3 통합본) |

**검증 항목**
- [ ] 합계 == 100
- [ ] `dataset` 필드값 == `gaia_dataset`
- [ ] 여러 회사의 문서가 섞여 있음 (단일 회사 집중 아님)

---

### TC-S-02  파일 유형 다양성

**검증 항목**
- [ ] XML 포함
- [ ] PDF 포함
- [ ] XLS 포함 (gaia_dataset 내 4,182개 존재)
- [ ] `source_type` 종류 >= 2

---

### TC-S-03 ~ TC-S-06  기타 샘플러 검증

| ID | 검증 내용 | 기대 결과 |
|----|-----------|-----------|
| TC-S-03 | 모든 샘플 `text_length >= 300` | PASS |
| TC-S-04 | 모든 샘플 `len(text) <= 8000` | PASS |
| TC-S-05 | `seed=42` 두 번 실행 시 동일 결과 | PASS |
| TC-S-06 | `seed=42` vs `seed=99` 결과 불일치 | PASS (달라야 함) |

---

## TC-Q  QA 생성 테스트

### TC-Q-01  XML 공시 문서 QA 생성

| 설정 | 값 |
|------|----|
| 문서 유형 | 사업보고서(A) XML |
| `n_qa` | 2 |

**검증 항목**
- [ ] QA 쌍 2개 생성
- [ ] `question`, `answer`, `question_type` 필드 존재
- [ ] `question_type` ∈ `{factual, summary, financial, relationship}`
- [ ] `question` 길이 >= 10자, `answer` 길이 >= 10자
- [ ] 답변에 회사명 또는 관련 키워드 포함

---

### TC-Q-02  XLS 재무제표 QA 생성

**검증 항목**
- [ ] 수치 포함 질문 생성 (매출액, 영업이익, 자산 등)
- [ ] `question_type == 'financial'` 포함
- [ ] 억원 단위 수치 또는 구체적 금액 포함

---

### TC-Q-03 ~ TC-Q-06  기타 QA 생성 검증

| ID | 검증 내용 | 기대 결과 |
|----|-----------|-----------|
| TC-Q-03 | PDF 공시 문서 → QA 2개 생성 | PASS |
| TC-Q-04 | 중단 후 재개 — 기존 QA 유지, 중복 없음 | PASS |
| TC-Q-05 | Rate Limit → max_retries=3 내 재시도 | PASS |
| TC-Q-06 | LLM 응답이 코드블록일 때 JSON 파싱 성공 | PASS |

---

## TC-R  RAGAS 포맷 테스트

### TC-R-01  필수 필드 존재

**검증 항목 (샘플 1개 기준)**
- [ ] `user_input` — 비어있지 않음
- [ ] `reference` — 비어있지 않음
- [ ] `retrieved_contexts` — list 타입
- [ ] `response` — 존재 (빈 문자열 허용)

---

### TC-R-02 ~ TC-R-05  기타 포맷 검증

| ID | 검증 내용 | 기대 결과 |
|----|-----------|-----------|
| TC-R-02 | `total_samples >= 100` | PASS |
| TC-R-03 | CSV 컬럼 11개 / UTF-8 BOM 인코딩 | PASS |
| TC-R-04 | HuggingFace Dataset 저장 및 로드 | PASS |
| TC-R-05 | `source_type`에 xml/pdf/xls 모두 포함 | PASS |

---

## TC-G  GAIA 쿼리 시나리오

> **전제**: 공시 문서가 Cohesity GAIA에 인덱싱 완료된 상태  
> **평가 기준**: Containment ≥ 0.5, RAGAS faithfulness ≥ 0.7

### TC-G-01  사업보고서(A) — 요약 쿼리

**예시 질문**
- 삼성전자의 2023년 사업보고서에서 주요 사업 부문은?
- 현대자동차의 최근 사업보고서 기준 종업원 수는?

| 목표 메트릭 | 기준 |
|------------|------|
| `faithfulness` | ≥ 0.7 |
| `answer_relevancy` | ≥ 0.6 |

---

### TC-G-02  공정위공시(J) — 관계 파악 쿼리

**예시 질문**
- 고려아연의 계열회사 간 주식소유 현황에서 서린상사의 지분율은?
- 영풍그룹 기업집단의 대표회사는?

| 목표 메트릭 | 기준 |
|------------|------|
| `faithfulness` | ≥ 0.7 |

---

### TC-G-03  주요사항보고서(B) — 사실 확인 쿼리

**예시 질문**
- 롯데쇼핑의 2021년 정정 계약서 공시의 정정 대상 공시서류는?
- SK케미칼의 유상증자 결정 주요사항보고서에서 신주 발행 목적은?

| 목표 메트릭 | 기준 |
|------------|------|
| `answer_relevancy` | ≥ 0.6 |

---

### TC-G-04  재무제표 수치 쿼리 (XLS 기반)

**예시 질문**
- 고려아연의 2014년 연결 재무상태표 기준 유동자산 총액은?
- 한국전력공사의 2023년 3분기 매출액은?
- 현대자동차의 직전 사업연도 대비 영업이익 증감은?

| 목표 메트릭 | 기준 |
|------------|------|
| `faithfulness` | ≥ 0.8 |
| 수치 Exact Match | 일치 |

---

### TC-G-05  기간 조건 쿼리

**예시 질문**
- 2021년에 제출된 기아의 공정위공시 내용은?
- 2015~2020년 사이 고려아연이 제출한 사업보고서 건수는?

| 목표 메트릭 | 기준 |
|------------|------|
| `context_precision` | ≥ 0.6 |

---

### TC-G-06  대기업 그룹 쿼리

**예시 질문**
- 한화그룹 계열사 중 DART에 공시된 회사 목록은?
- 삼성전자와 삼성생명의 공시 유형별 건수 비교

| 목표 메트릭 | 기준 |
|------------|------|
| `context_recall` | ≥ 0.5 |

---

### TC-G-07  다중 문서 종합 쿼리

**예시 질문**
- SK텔레콤의 공정위공시와 사업보고서에서 공통적으로 언급되는 계열사는?
- 현대모비스의 최근 3개년 영업이익 추이는?

| 목표 메트릭 | 기준 |
|------------|------|
| `faithfulness` | ≥ 0.7 |
| `context_recall` | ≥ 0.6 |

---

### TC-G-08  문서 유형 혼합 쿼리 (XML + PDF + XLS)

**예시 질문**
- 한국전력공사의 분기보고서에서 재무제표상 영업손실과 공시 본문의 손실 원인 설명을 종합하면?

| 목표 메트릭 | 기준 |
|------------|------|
| `faithfulness` | ≥ 0.7 |

---

## TC-E  엣지 케이스

| ID | 시나리오 | 검증 항목 | 기대 결과 |
|----|----------|-----------|-----------|
| TC-E-01 | 스캔 이미지 PDF (텍스트 추출 불가) | `parse_file()` → `(None, 'none')`, 샘플러 건너뜀 | PASS |
| TC-E-02 | 손상된 XLS 파일 | `parse_xls_text()` → `None`, 예외 전파 없음 | PASS |
| TC-E-03 | 텍스트 극히 짧은 공시 (< 300자) | `min_length` 필터 제외 | PASS |
| TC-E-04 | 한글 깨짐 없음 | JSON `ensure_ascii=False`, CSV UTF-8 BOM | PASS |
| TC-E-05 | 중복 샘플링 방지 | 100개 샘플의 `file_path` 모두 고유 | PASS |
| TC-E-06 | QA 생성 시 빈 텍스트 | 해당 문서 건너뜀, 오류 없음 | PASS |

---

## TC-I  통합 파이프라인 테스트

### TC-I-01  Mini 파이프라인 (API 소량 사용)

```bash
python test_pipeline.py --stage all
```

| 설정 | 값 |
|------|----|
| `sample_size` | 5 |
| `qa_per_doc` | 2 |

**검증 항목**
- [ ] `sampled_documents.json` 생성 (5개)
- [ ] `qa_pairs.json` 생성 (≥ 5개)
- [ ] `ragas_testset.json` 생성 (필수 필드 존재)
- [ ] `gaia_eval.csv` 생성 (5+ 행)
- [ ] 전체 실행 시간 < 3분

---

### TC-I-02  Full 파이프라인 (100문서)

```bash
./run.sh
```

**검증 항목**
- [ ] `sampled_documents.json`: 100개
- [ ] `qa_pairs.json`: ≥ 100개
- [ ] `ragas_testset.json`: `total_samples >= 100`
- [ ] `gaia_eval.csv`: ≥ 100행
- [ ] 전체 실행 시간 < 30분

---

### TC-I-03  중단 후 재개

1. `./run.sh --step qa` 실행 중 50번째에서 강제 종료 (`Ctrl+C`)
2. 동일 명령 재실행

**검증 항목**
- [ ] 기존 50개 QA 유지
- [ ] 나머지 50개만 추가 생성 (중복 없음)
- [ ] 최종 결과 == 처음부터 실행한 결과

---

### TC-I-04  RAGAS 평가 실행 (GAIA API 필요)

```bash
./run.sh --step evaluate
```

**검증 항목**
- [ ] `gaia_eval_results.csv` 생성
- [ ] `ragas_eval_results.csv` 생성

**목표 수치**

| 메트릭 | 목표 |
|--------|------|
| Exact Match | ≥ 20% |
| Avg Containment | ≥ 0.4 |
| `faithfulness` | ≥ 0.7 |
| `answer_relevancy` | ≥ 0.6 |
| `context_precision` | ≥ 0.5 |
| `context_recall` | ≥ 0.5 |

---

## 합격 기준 요약

| 카테고리 | 테스트 수 | 합격 기준 |
|----------|-----------|-----------|
| TC-P 파서 | 7 | 전체 PASS |
| TC-S 샘플러 | 6 | 전체 PASS |
| TC-Q QA 생성 | 6 | 5/6 이상 PASS |
| TC-R RAGAS 포맷 | 5 | 전체 PASS |
| TC-G GAIA 쿼리 | 8 | 목표 메트릭 6/8 이상 달성 |
| TC-E 엣지케이스 | 6 | 전체 PASS |
| TC-I 통합 | 4 | TC-I-01~03 PASS + TC-I-04 목표 수치 달성 |

---

## 실행 명령어 요약

### 로컬 실행 (venv)

```bash
cd /data/richard/cohesity-poc/gaia_ragas

# Stage 1: 파서/샘플러 (API 불필요, ~5초)
.venv/bin/python test_pipeline.py --stage parse

# Stage 2: QA 생성 소규모 (Claude API, ~30초)
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python test_pipeline.py --stage qa

# Stage 3: RAGAS 포맷 검증 (~3초)
.venv/bin/python test_pipeline.py --stage testset

# 전체 Mini 파이프라인 (5문서, ~2분)
.venv/bin/python test_pipeline.py --stage all

# 전체 Full 파이프라인 — A안: 전체 랜덤
./run.sh

# 전체 Full 파이프라인 — B안: 특정 회사
./run.sh --step sample --company 삼성전자
./run.sh --step qa     --company 삼성전자
```

### Docker 실행 (컨테이너)

```bash
cd /data/richard/cohesity-poc

# 사전: .env 파일에 ANTHROPIC_API_KEY 설정
# docker compose build (최초 1회)

# A안: 전체 랜덤 샘플링
docker compose run --rm gaia-ragas ./run.sh --step sample
docker compose run --rm gaia-ragas ./run.sh --step qa
docker compose run --rm qdrant-rag ./run.sh ingest
docker compose run --rm qdrant-rag ./run.sh evaluate

# B안: 특정 회사 (권장)
docker compose run --rm gaia-ragas ./run.sh --step sample --company 삼성전자
docker compose run --rm gaia-ragas ./run.sh --step qa     --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh ingest        --company 삼성전자
docker compose run --rm qdrant-rag ./run.sh evaluate      --company 삼성전자 --limit 20

# 검색 / Q&A 단독 테스트
docker compose run --rm qdrant-rag ./run.sh search "삼성전자 2021년 영업이익"
docker compose run --rm qdrant-rag ./run.sh qa "LG화학 배터리 사업 분할 내용은?"

# GAIA 클러스터 평가 (별도 클러스터 필요)
export COHESITY_CLUSTER_URL=https://<cluster-ip>
export COHESITY_API_TOKEN=<bearer-token>
docker compose run --rm gaia-ragas ./run.sh --step evaluate
```

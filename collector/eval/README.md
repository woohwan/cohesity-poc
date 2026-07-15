# collector/eval — 문서 타입별/소스(토픽)별 QA/RAGAS 데이터셋 생성기

`collector/gaia_kr_collector`가 수집한 `cohesity-poc/gaia_web_dataset`(구 경로: `gaia_test_200g_kr80_no_ocr`) 데이터셋에서
문서 타입(PDF / DOCX·DOC / XLSX·XLS·CSV / PPT·PPTX)별로, 또는 수집 소스(=토픽,
`manifest.csv`의 `source` 컬럼 — 예: `dart_financial`, `bok_publications`)별로
QA 쌍을 생성하고 Cohesity GAIA RAGAS 평가용 테스트셋을 만드는 독립 파이프라인이다.
모든 단계는 `--group-by {type,source}`로 그룹 기준을 선택할 수 있다 (기본값 `type`).

`gaia_ragas/`(DART 공시 문서용, 회사 단위)와는 별개의 파이프라인이며 서로 의존하지 않는다.
txt 타입은 제외한다 — 수집된 txt의 대부분이 Common Crawl 저품질 스크랩이라 의미 있는
문서로 보기 어렵다고 판단해 제외했다 (자세한 배경은 이 저장소의 대화 기록 참고).

## 처리 흐름

```
gaia_web_dataset/  (pdf/docx/xlsx/pptx 등)
      │
      ▼
document_sampler.py   →  타입별 무작위 샘플링 + 텍스트 추출 (parsers.py)
      │
      ▼
qa_generator.py        →  LLM API로 문서당 QA 쌍 생성 (기본 2개)
      │
      ▼
ragas_testset_creator.py  →  RAGAS SingleTurnSample 변환 + GAIA 평가 CSV + HF Dataset
      │
      ▼
gaia_evaluator.py      →  (별도 실행) Cohesity GAIA API 질의 + RAGAS 스타일 채점
```

## 1. 사전 준비

### 1-1. 원본 데이터셋

이 파이프라인은 `cohesity-poc/gaia_web_dataset/`(collector/eval 기준 `../../gaia_web_dataset/`)를
기본 입력으로 삼는다 (2026-07-15 이전: `collector/gaia_kr_collector/gaia_test_200g_kr80_no_ocr/`).
다른 머신에서 돌리려면 아래 중 하나가 필요하다.

- `collector/gaia_kr_collector`를 먼저 돌려서 그 결과물을 만들거나,
- 이미 수집된 `gaia_web_dataset/` 디렉터리를 통째로 복사해오거나,
- 경로가 다르면 `GAIA_KR_DATASET_DIR` 환경 변수로 위치를 지정한다 (아래 참고).

### 1-2. 시스템 패키지 — LibreOffice (필수는 아니지만 권장)

구형 포맷(`.doc`, `.ppt`, `.odf`, `.rtf`)은 `python-docx`/`python-pptx`가 못 읽어서
LibreOffice headless 변환으로 폴백한다. 없어도 파이프라인은 동작하지만 그 타입의
샘플링 가능한 문서 수가 줄어든다 (예: 수집 데이터에 구형 `.doc`/`.ppt`가 섞여 있는 경우).

```bash
sudo apt install -y libreoffice-core libreoffice-writer libreoffice-impress
which soffice   # 경로 확인
```

### 1-3. Python 가상환경

```bash
cd collector/eval
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
```

Python 3.10+ 권장 (3.12에서 검증됨).

### 1-4. LLM API 키

QA 생성 단계(`--step qa`)에서만 필요하다. 환경 변수로 직접 넘긴다 (`.env` 파일을 쓰지
않는다 — `gaia_ragas/.env`와는 별개이며 서로 참조하지 않는다).

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# 필요 시:
# export LLM_PROVIDER=claude          # claude(기본) | chatgpt
# export CLAUDE_MODEL=claude-sonnet-4-6
# export OPENAI_API_KEY="sk-..."      # LLM_PROVIDER=chatgpt 일 때
# export OPENAI_MODEL=gpt-5-mini
```

## 2. 실행

전체 파이프라인(샘플링 → QA 생성 → RAGAS 테스트셋)을 4개 타입 모두에 대해 실행:

```bash
cd collector/eval
.venv/bin/python run_pipeline.py --step all
```

특정 타입만 실행 (`pdf` / `docx_doc` / `xlsx_xls_csv` / `ppt_pptx`):

```bash
.venv/bin/python run_pipeline.py --step all --type ppt_pptx
```

타입당 샘플 문서 수 조정 (기본 100):

```bash
.venv/bin/python run_pipeline.py --step all --sample-size 50
```

소스(=토픽) 단위로 실행 — 전체 소스(약 74개, 소스당 기본 20개 샘플):

```bash
.venv/bin/python run_pipeline.py --step all --group-by source
```

특정 소스만:

```bash
.venv/bin/python run_pipeline.py --step all --group-by source --source dart_financial
```

`--type`은 `--group-by type`(기본값)에서만, `--source`는 `--group-by source`에서만 쓸 수 있다.
소스 목록은 `document_sampler.list_sources()`가 `DATASET_DIR` 바로 아래 디렉터리를 훑어 반환한다.

### 단계별 실행

각 단계는 독립적으로도 실행 가능하고, 이전 단계의 출력 파일(`output/*.json`)을 읽는다.

```bash
# 1) 타입별 문서 샘플링 + 텍스트 추출 (LLM 불필요)
.venv/bin/python run_pipeline.py --step sample

# 2) QA 쌍 생성 (LLM 호출 — ANTHROPIC_API_KEY 필요)
.venv/bin/python run_pipeline.py --step qa

# 3) RAGAS 테스트셋 변환 (LLM 불필요)
.venv/bin/python run_pipeline.py --step testset
```

개별 모듈을 직접 실행할 수도 있다 (동일한 `--type`/`--source` 인자 지원):

```bash
.venv/bin/python document_sampler.py --type pdf --sample-size 30
.venv/bin/python qa_generator.py --type pdf
.venv/bin/python ragas_testset_creator.py --type pdf

# 소스(토픽) 기준
.venv/bin/python document_sampler.py --group-by source --source dart_financial --sample-size 20
.venv/bin/python qa_generator.py --source dart_financial
.venv/bin/python ragas_testset_creator.py --source dart_financial
```

### 재개(resume)

`qa_generator.py`는 `output/qa_pairs_{type}.json`에 이미 처리된 `doc_id`를 기록해두고
10개 문서마다 중간 저장하므로, 중간에 중단돼도 다시 실행하면 이어서 처리한다.

## 3. Cohesity GAIA 평가 실행

앞 단계에서 만든 `ragas_testset_{type}.json`을 가지고 **실제 Cohesity GAIA에 질의를 보내
답변을 받아오고, RAGAS 스타일 지표로 채점**하는 단계다. `gaia_evaluator.py`가 담당한다.

### 3-1. 전제 조건

1. 이 저장소의 문서(`gaia_web_dataset`)가 **먼저 Cohesity GAIA 데이터셋으로
   색인(ingest)되어 있어야 한다.** 이 스크립트는 질의만 하지, 색인은 하지 않는다
   (색인은 Helios UI 또는 별도 ingestion API로 진행 — 이 저장소 범위 밖).
2. GAIA API 접근 정보를 환경 변수로 설정한다:

```bash
export COHESITY_CLUSTER_URL="https://<helios-fqdn>"
export COHESITY_API_KEY="<Helios API Key>"          # Settings > Access Management > API Keys
export COHESITY_DATASET_NAME="<GAIA 데이터셋 이름>"   # 위 1번에서 색인한 데이터셋 이름
```

`gaia_ragas/.env`에 이미 같은 클러스터의 값이 있더라도 **참조하지 않는다** — 이 셸에
직접 `export`해야 한다. (`COHESITY_DATASET_NAME`은 `gaia_ragas`가 쓰는 DART 데이터셋과는
다른, 이 수집 데이터 전용 데이터셋 이름이어야 의미가 있다.)

### 3-2. 실행

```bash
cd collector/eval
.venv/bin/python run_pipeline.py --step evaluate --type ppt_pptx
```

`--type` 생략 시 4개 타입 모두에 대해 순서대로 실행된다. 타입당 GAIA API 200개 질의 +
RAGAS 채점(LLM+임베딩 기반) 200개가 나가므로 비용/시간이 꽤 든다 — 우선 소규모로
확인하려면:

```bash
.venv/bin/python run_pipeline.py --step evaluate --type ppt_pptx --max-samples 10
```

내부적으로 두 단계가 순서대로 돈다 (개별 실행도 가능):

```bash
# 1) GAIA API 질의 → gaia_eval_results_{type}.csv (question, gaia_response, exact_match, containment 등)
.venv/bin/python gaia_evaluator.py --type ppt_pptx --max-samples 10

# 2) 위 결과에 pip 패키지 ragas로 RAGAS 4개 지표 채점 → ragas_eval_results_{type}.csv
#    (1번 안에서 자동으로 이어서 실행됨. --skip-ragas로 건너뛸 수 있음)
```

### 3-3. RAGAS 지표 — pip 패키지 `ragas` 사용 (+ 설치 문제 우회)

`gaia_ragas`와 동일하게 pip 패키지 `ragas` + `langchain`으로 faithfulness/
answer_relevancy/context_precision/context_recall을 계산한다
(`ragas.evaluate()`, LLM은 Claude, 임베딩은 OpenAI fallback을 피하기 위해
`paraphrase-multilingual-MiniLM-L12-v2` 사용 — `gaia_ragas`와 동일한 선택).

**설치 시 주의할 점**: 현재 PyPI의 `ragas`(0.2.x~0.4.x 전부 해당)는
`ragas/llms/base.py`에서 무조건
`from langchain_community.chat_models.vertexai import ChatVertexAI`를 import하는데,
최신 `langchain-community`(0.4.x)에는 이 서브모듈이 이미 제거돼 있어 **`pip install`만으로는
`ragas`를 import하는 순간 `ModuleNotFoundError`가 난다** (2026-07-09 확인). 오래된
`langchain-community`를 같이 깔면 이번엔 최신 `langchain-core`(ragas가 요구)와 버전이
충돌한다.

`ChatVertexAI`는 ragas 내부에서 타입 체크용 리스트에만 들어가고 이 프로젝트는
VertexAI를 전혀 쓰지 않으므로(Claude만 사용), **`_ragas_compat.py`가 `sys.modules`에
더미 `ChatVertexAI` 클래스를 등록해 import만 통과시키는 shim** 역할을 한다.
`gaia_evaluator.py` 맨 위에서 `ragas`보다 먼저 이 shim을 import하도록 이미 되어 있어서
**따로 신경 쓸 필요 없이 `pip install -r requirements.txt` 후 바로 동작한다.**
(직접 다른 스크립트에서 `ragas`를 import하려면 그 전에 `import _ragas_compat`을 먼저
호출해야 한다.)

### 3-4. 결과 확인

```bash
.venv/bin/python -c "
import pandas as pd
df = pd.read_csv('output/ragas_eval_results_ppt_pptx.csv')
print(df[['faithfulness','answer_relevancy','context_precision','context_recall']].mean())
"
```

## 4. 소요 시간 / 비용 감

(타입당 문서 100개, QA 2개/문서, Claude 기준으로 실측한 값 — 참고용)

| 단계 | 타입당 소요 | 비고 |
|---|---|---|
| sample | PDF ~3분, DOCX/PPT ~수분~수십분 | 구형 포맷 LibreOffice 변환 비율에 따라 편차 큼 |
| qa | ~10~15분 | 문서당 LLM 호출 1회, ~7초/문서 |
| testset | 수 초 | LLM 미사용 |

4개 타입 전체(`--step all`, 기본 100개)는 총 40분~1시간 정도 잡으면 된다.
급하면 `--sample-size`를 줄여서(예: 30) 빠르게 돌려볼 수 있다.

`--step evaluate`는 타입당(QA 200개 기준) GAIA API 호출 200회 + RAGAS 채점 200개가
나간다 — GAIA 응답 속도에 좌우되지만 대략 타입당 10~20분 정도로 잡으면 된다.
(`sentence-transformers` 임베딩 모델은 최초 1회만 다운로드되고 이후엔 캐시에서 로드된다.)

## 5. 문제 해결

- **샘플링이 특정 파일에서 멈춘 것처럼 보임**: `parsers.py`의 pdf/docx/xlsx/pptx 파서에
  30초 타임아웃이 걸려 있어 자동으로 스킵되고 다음 파일로 넘어간다. 그래도 안 넘어가면
  `ps aux | grep run_pipeline`로 실제 프로세스가 살아있는지 확인.
- **`ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다`**: `--step qa`/`evaluate` 실행 전에
  `export ANTHROPIC_API_KEY=...`가 현재 셸에 설정돼 있는지 확인 (다른 셸/nohup 환경이면
  누락되기 쉽다).
- **`COHESITY_CLUSTER_URL, COHESITY_API_KEY 환경 변수를 설정하세요`**: `--step evaluate`
  실행 전 3-1절의 `COHESITY_*` 3개 환경 변수를 export했는지 확인.
- **`ModuleNotFoundError: langchain_community.chat_models.vertexai`**: `gaia_evaluator.py`가
  `_ragas_compat`을 `ragas`보다 먼저 import하지 않은 경우에만 발생한다 (직접 다른 스크립트에서
  `ragas`를 import했다면 그 스크립트 맨 위에 `import _ragas_compat`을 추가). 3-3절 참고.
- **`Warning: You are sending unauthenticated requests to the HF Hub`**: 무시해도 된다
  (속도 제한 완화용 경고일 뿐). 필요하면 `export HF_TOKEN=...`으로 없앨 수 있음.
- **LibreOffice 관련 변환 실패**: `.doc`/`.ppt` 등 구형 파일은 LibreOffice가 없으면
  조용히 스킵(None 반환)된다 — 파이프라인이 죽지는 않지만 해당 타입 샘플이 줄어든다.
- **`gaia_web_dataset`를 못 찾음**: `GAIA_KR_DATASET_DIR` 환경 변수로 절대
  경로를 직접 지정.

```bash
export GAIA_KR_DATASET_DIR=/path/to/gaia_web_dataset
```

## 6. 출력 (`output/`)

`{group}`은 `--group-by type`이면 타입명(`pdf` / `docx_doc` / `xlsx_xls_csv` / `ppt_pptx`),
`--group-by source`이면 소스명(`dart_financial` / `bok_publications` 등)이다.

| 파일 | 생성 단계 | 설명 |
|---|---|---|
| `sampled_documents_{group}.json` | sample | 샘플링된 문서 원문 + 메타데이터 |
| `qa_pairs_{group}.json` | qa | 문서별 QA 쌍 (재사용 가능 — 재실행 시 이어서 생성) |
| `ragas_testset_{group}.json` | testset | RAGAS SingleTurnSample 포맷 테스트셋 |
| `gaia_eval_{group}.csv` | testset | (참고용) 질문/정답만 담은 GAIA 평가 입력 CSV — 실제 응답 아님 |
| `ragas_testset_hf_{group}/` | testset | HuggingFace `datasets` 형식 (설치돼 있으면 생성) |
| `gaia_eval_results_{group}.csv` | evaluate | **실제 GAIA 응답** + exact_match/containment |
| `ragas_eval_results_{group}.csv` | evaluate | LLM judge로 채점한 4개 RAGAS 스타일 지표 |

각 레코드에는 그룹 기준과 무관하게 `type_group`(문서 타입)과 `source`(수집 소스) 필드가
항상 함께 들어있으므로, 소스 기준으로 생성한 데이터셋도 타입별 분포를 그대로 확인할 수 있다
(반대도 마찬가지).

## 7. 설정값 (`config.py`)

| 변수 | 기본값 | 환경 변수 | 설명 |
|---|---|---|---|
| `DATASET_DIR` | `../../gaia_web_dataset` | `GAIA_KR_DATASET_DIR` | 샘플링 대상 원본 데이터 경로 |
| `OUTPUT_DIR` | `./output` | `OUTPUT_DIR` | 결과 저장 경로 |
| `LLM_PROVIDER` | `claude` | `LLM_PROVIDER` | `claude` 또는 `chatgpt` |
| `SAMPLE_SIZE_PER_TYPE` | `100` | `SAMPLE_SIZE_PER_TYPE` | 타입당 샘플 문서 수 (`--group-by type`) |
| `SAMPLE_SIZE_PER_SOURCE` | `20` | `SAMPLE_SIZE_PER_SOURCE` | 소스당 샘플 문서 수 (`--group-by source`) |
| `MIN_TEXT_LENGTH` | `300` | — | 최소 텍스트 길이 (미만 제외) |
| `MAX_TEXT_LENGTH` | `8000` | — | LLM 입력 최대 텍스트 길이 |
| `QA_PER_DOC` | `2` | — | 문서당 QA 쌍 수 |

`gaia_evaluator.py`가 쓰는 `COHESITY_CLUSTER_URL` / `COHESITY_API_KEY` /
`COHESITY_DATASET_NAME`은 `config.py`가 아니라 환경 변수로만 읽는다 (3-1절 참고).

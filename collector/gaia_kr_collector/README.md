# Gaia 한국어 200GB 데이터 수집기

목표 조건:

- SEC 제외
- DART 제외
- AI Hub OCR/이미지 기반 데이터 제외
- 총 200GB
- 한국어 80% 이상
- 문서 종류: PDF/XLSX/CSV/XML/JSON/TXT/DOCX/DOC 이외의 파일은 제외
- archive 파일 제외: zip, bz2 등
- 한글 문서 제외: hwp, hwpx

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 수집 계획 확인

```bash
python gaia_collect.py --config config.yaml --plan
```

## 전체 수집

```bash
python gaia_collect.py --config config.yaml --run all
```

## 단계별 수집 추천

```bash
python gaia_collect.py --config config.yaml --run national_assembly_reports
python gaia_collect.py --config config.yaml --run gov_policy_reports
python gaia_collect.py --config config.yaml --run kosis_statistics
python gaia_collect.py --config config.yaml --run data_go_kr
python gaia_collect.py --config config.yaml --run kowiki_knowledge
python gaia_collect.py --config config.yaml --run common_crawl_ko_text
python gaia_collect.py --config config.yaml --run english_reference
```

> **kowiki_knowledge 참고**: Wikipedia 덤프(`.bz2`)를 다운로드한 후 자동으로 XML을 파싱하여 `.docx` 파일로 변환합니다.
> 출력 위치: `kowiki_knowledge/docx/kowiki_00000.docx` ~ (200개 문서당 1파일)

## 용량 목표

| 소스 | 목표 |
|---|---:|
| 공공데이터포털 | 50GB |
| 정부/공공기관 정책·연구보고서 | 45GB |
| KOSIS/통계청 | 25GB |
| 국회입법조사처/예산정책처 | 20GB |
| Common Crawl 한국어 텍스트 | 15GB |
| 한국어 Wikipedia | 10GB |
| 영어 기준 데이터 | 35GB |
| **합계** | **200GB** |

## 중요한 운영 팁

1. 공공데이터포털과 KOSIS는 일부 데이터가 API 승인 또는 로그인/키를 요구합니다. 이 경우 `config.yaml`에 구체적인 파일 다운로드 URL을 추가하세요.
2. 정부·국회 사이트는 HTML 구조가 바뀔 수 있으므로, 수집량이 적으면 seed URL을 더 추가하세요.
3. Common Crawl은 노이즈가 많습니다. `min_hangul_ratio`를 0.15~0.30 사이에서 조정하세요.
4. 스캔 PDF는 최대한 제외하세요. Gaia 테스트에는 텍스트 선택 가능한 PDF가 좋습니다.
5. 수집 이력은 `manifest.csv`에 저장됩니다.

## 출력 구조

```text
gaia_test_200g_kr80_no_ocr/
  data_go_kr/
  gov_policy_reports/
  kosis_statistics/
  national_assembly_reports/
  common_crawl_ko_text/
    filtered_ko_txt/  ← 한국어 필터링된 텍스트 청크 (.txt)
    wet_raw/          ← 처리 중간 WET 파일 (임시, .warc.wet.gz)
  kowiki_knowledge/
    raw/        ← Wikipedia 덤프 원본 (.bz2)
    docx/       ← 변환된 문서 (kowiki_00000.docx ~ )
  english_reference/
  manifest.csv
```

## Cohesity Gaia 적재 흐름

1. 위 디렉토리를 NAS/SMB/NFS/파일서버 또는 VM 디스크에 저장
2. Cohesity에서 해당 소스를 등록
3. Protection Group 생성 후 백업 수행
4. Gaia에서 백업 스냅샷 기반 Dataset 생성
5. Dataset별 질문셋으로 Q&A/Citation/검색 정확도 평가

# Gaia 한국어 200GB 데이터 수집기

목표 조건:

- SEC 제외
- DART 제외
- AI Hub OCR/이미지 기반 데이터 제외
- 총 200GB
- 한국어 60% 이상
- 수집 대상: PDF / XLSX / CSV / XML / JSON / TXT / DOCX / DOC
- HWP / HWPX: LibreOffice(`libreoffice-h2orestart`)로 DOCX 변환 후 수집 (원본 보존, 변환 성공률 ~95%)
- archive 파일 제외: zip, bz2 등

## 용량 목표

쿼터는 소스가 아닌 **문서 타입 단위**로 관리됩니다. 소스별 실행 순서나 반복 횟수는 제한이 없으며, 타입 목표가 채워지면 해당 타입의 파일 다운로드를 자동으로 건너뜁니다.

| 문서 타입 | 목표 | 비고 |
|---|---:|---|
| PDF | 100GB | 텍스트 선택 가능한 PDF만 (스캔 PDF 제외) |
| DOCX / DOC | 40GB | |
| XLSX / XLS / CSV | 30GB | |
| TXT | 20GB | |
| JSON / XML | 10GB | |
| **합계** | **200GB** | 한국어 60% 이상 유지 |

## 운영 팁

1. 공공데이터포털과 KOSIS는 일부 데이터가 API 승인 또는 로그인/키를 요구합니다. 이 경우 `config.yaml`에 구체적인 파일 다운로드 URL을 추가하세요.
2. 정부·국회 사이트는 HTML 구조가 바뀔 수 있으므로, 수집량이 적으면 `seed_urls`를 더 추가하세요.
3. Common Crawl은 노이즈가 많습니다. `min_hangul_ratio`를 0.15~0.30 사이에서 조정하세요.
4. 스캔 PDF는 최대한 제외하세요. Gaia 테스트에는 텍스트 선택 가능한 PDF가 좋습니다.

## 출력 구조

`config.yaml`의 `root_dir`이 가리키는 경로에 저장된다 (2026-07-15부터
`cohesity-poc/gaia_web_dataset/`, 구 경로: `./gaia_test_200g_kr80_no_ocr`).

```text
gaia_web_dataset/
  data_go_kr/
  gov_policy_reports/
  kosis_statistics/
  national_assembly_reports/
  common_crawl_ko_text/
    filtered_ko_txt/  ← 한국어 필터링된 텍스트 청크 (.txt)
    wet_raw/          ← WET 다운로드 임시 경로 (처리 후 즉시 삭제, 평소 비어있음)
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

---

## 실행

```bash
./run.sh setup        # 가상환경 생성 및 패키지 설치
./run.sh plan         # 타입별 수집 현황 확인
./run.sh bg           # 백그라운드 수집 시작 (전체 소스)
./run.sh status       # 프로세스 상태 + 로그 + 현황
./run.sh log          # 로그 실시간 출력
./run.sh stop         # 수집 중단
./run.sh cleanup      # 임시/중간 파일 정리 (wet_raw, .bin, 이미지)
```

특정 소스만 실행하거나 포그라운드로 실행할 수도 있습니다.

```bash
./run.sh run gov_policy_reports data_go_kr   # 포그라운드, 지정 소스
./run.sh bg  gov_policy_reports data_go_kr   # 백그라운드, 지정 소스
```

재시작하면 `manifest.csv` 기준으로 이미 받은 파일은 건너뛰고 이어받습니다.

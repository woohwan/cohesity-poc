"""
test_dart_pdf.py
================

KOSPI 시총 상위 회사 중 5개사 대상으로 DART 정기공시 PDF 다운로드 테스트
- KRX OpenAPI 종목 조회 검증된 후 실행
- 본 다운로더 실행 전 동작 확인용

사용법:
  python test_dart_pdf.py
"""

import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

from krx_dart_common import (
    KRX_AUTH_KEY, DART_API_KEY,
    get_kospi200_stocks,
    fetch_dart_corp_codes, search_dart_filings,
    make_dart_session, download_dart_pdf,
    safe_filename,
)


# 테스트 대상 회사 수 / 회사당 최대 PDF 수
TEST_COMPANIES = 5
MAX_PDFS_PER_COMPANY = 2

OUTPUT_DIR = Path("./test_pdfs")
OUTPUT_DIR.mkdir(exist_ok=True)

# 최근 1년
END_DATE = datetime.now().strftime("%Y%m%d")
START_DATE = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def main():
    print("=" * 60)
    print("DART PDF 다운로드 테스트")
    print(f"  대상: KOSPI 시총 상위 {TEST_COMPANIES}개사")
    print(f"  회사당 최대: {MAX_PDFS_PER_COMPANY}개 PDF")
    print(f"  기간: {START_DATE} ~ {END_DATE}")
    print("=" * 60)

    # 키 확인
    if not KRX_AUTH_KEY or KRX_AUTH_KEY.startswith("여기에"):
        print("✗ KRX_AUTH_KEY 미설정")
        sys.exit(1)
    if not DART_API_KEY or DART_API_KEY.startswith("여기에"):
        print("✗ DART_API_KEY 미설정")
        sys.exit(1)

    # 1) KOSPI 시총 상위 N개
    print("\n[1] KRX에서 시총 상위 종목 조회...")
    kospi200 = get_kospi200_stocks()
    targets_krx = kospi200.head(TEST_COMPANIES)
    print(f"  대상: {len(targets_krx)}개")
    for _, row in targets_krx.iterrows():
        print(f"    - {row['stock_code']} {row['stock_name']} "
              f"({row['marcap']/1e12:.1f}조원)")

    # 2) DART corp_code 매핑
    print("\n[2] DART corp_code 매핑 다운로드...")
    dart_corps = fetch_dart_corp_codes()
    targets = targets_krx.merge(dart_corps, on="stock_code", how="inner")
    print(f"  매칭 완료: {len(targets)}개")

    if len(targets) == 0:
        print("  ✗ 매칭된 회사가 없습니다.")
        sys.exit(1)

    # 3) PDF 다운로드
    print("\n[3] DART PDF 다운로드...")
    session = make_dart_session()
    results = []

    for _, row in targets.iterrows():
        print(f"\n  [{row['corp_name']}] 정기공시 검색...")
        filings = search_dart_filings(
            row["corp_code"], START_DATE, END_DATE
        )
        print(f"    검색 결과: {len(filings)}건")

        company_dir = OUTPUT_DIR / safe_filename(
            f"{row['stock_code']}_{row['corp_name']}"
        )

        # 회사당 최대 N개만
        for f in filings[:MAX_PDFS_PER_COMPANY]:
            report_nm = safe_filename(f["report_nm"])[:60]
            filename = f"{f['rcept_dt']}_{report_nm}_{f['rcept_no']}.pdf"
            save_path = company_dir / filename

            print(f"    다운로드: {f['report_nm']} ({f['rcept_dt']})")
            t0 = time.time()
            success = download_dart_pdf(session, f["rcept_no"], save_path)
            dt = time.time() - t0

            if success:
                size_mb = save_path.stat().st_size / 1024 / 1024
                print(f"      ✓ {size_mb:.1f}MB ({dt:.1f}초)")
            else:
                print(f"      ✗ 실패 ({dt:.1f}초)")

            results.append({
                "corp_name": row["corp_name"],
                "rcept_no": f["rcept_no"],
                "report_nm": f["report_nm"],
                "rcept_dt": f["rcept_dt"],
                "success": success,
            })
            time.sleep(1)  # 요청 간격

    # 4) 결과 요약
    print("\n" + "=" * 60)
    print("결과 요약")
    print("=" * 60)
    df = pd.DataFrame(results)
    if len(df) > 0:
        success = df["success"].sum()
        total = len(df)
        print(f"성공: {success}/{total}")
        print(f"저장: {OUTPUT_DIR.resolve()}")

        df.to_csv(OUTPUT_DIR / "_test_result.csv",
                  index=False, encoding="utf-8-sig")

        print("\n--- 다운로드된 파일 ---")
        for company_dir in sorted(OUTPUT_DIR.iterdir()):
            if company_dir.is_dir():
                pdfs = list(company_dir.glob("*.pdf"))
                if pdfs:
                    print(f"\n[{company_dir.name}]")
                    for pdf in sorted(pdfs):
                        size_mb = pdf.stat().st_size / 1024 / 1024
                        print(f"  - {pdf.name} ({size_mb:.1f}MB)")

        print("\n" + "=" * 60)
        if success == total:
            print("✓ 모두 성공! 본 다운로더 실행 가능")
            print("  python dart_kospi200_downloader.py")
        elif success > 0:
            print("⚠ 일부 성공. 실패 케이스 확인 후 본 다운로더 진행")
        else:
            print("✗ 전부 실패. DART 사이트 연결 점검 필요")
        print("=" * 60)
    else:
        print("결과 없음")


if __name__ == "__main__":
    main()

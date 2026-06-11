"""
test_kospi200_list.py
=====================

KRX OpenAPI로 KOSPI 시총 상위 200개 조회 테스트

사용법:
  1. krx_dart_common.py와 같은 폴더에 두기
  2. 환경변수 설정 (또는 krx_dart_common.py에 직접 입력)
       export KRX_AUTH_KEY="발급받은_키"
  3. python test_kospi200_list.py
"""

import sys
import logging
from pathlib import Path

import pandas as pd

from krx_dart_common import (
    KRX_AUTH_KEY,
    fetch_kospi_base_info,
    fetch_kospi_daily_trade,
    get_kospi200_stocks,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def main():
    print("=" * 60)
    print("KRX OpenAPI - KOSPI 시총 상위 200 조회 테스트")
    print("=" * 60)

    # 키 확인
    if not KRX_AUTH_KEY or KRX_AUTH_KEY.startswith("여기에"):
        print("\n✗ KRX_AUTH_KEY가 설정되지 않았습니다.")
        print("  방법 1: 환경변수")
        print('    export KRX_AUTH_KEY="발급받은_키"')
        print("  방법 2: krx_dart_common.py 상단 변수 직접 수정")
        sys.exit(1)

    # 1. 종목기본정보만 단독 호출 (스모크 테스트)
    print("\n[1/3] 유가증권 종목기본정보 호출 테스트...")
    try:
        base_df = fetch_kospi_base_info()
        print(f"  ✓ 성공: {len(base_df)}개")
        print(f"  컬럼: {list(base_df.columns)[:15]}...")
        if len(base_df) > 0:
            print(f"\n  첫 행 샘플:")
            print(base_df.iloc[0].to_dict())
    except Exception as e:
        print(f"  ✗ 실패: {e}")
        print("\n  ※ '유가증권 종목기본정보' 서비스 이용 신청이 안 됐을 수 있습니다.")
        print("    https://openapi.krx.co.kr 마이페이지에서 신청하세요.")
        sys.exit(1)

    # 2. 일별매매정보 (시가총액)
    print("\n[2/3] 유가증권 일별매매정보 호출 테스트...")
    try:
        trade_df = fetch_kospi_daily_trade()
        print(f"  ✓ 성공: {len(trade_df)}개")
        print(f"  컬럼: {list(trade_df.columns)[:15]}...")
        if "MKTCAP" in trade_df.columns:
            print(f"  ✓ MKTCAP(시가총액) 컬럼 확인")
        else:
            print(f"  ⚠ MKTCAP 없음. 사용 가능 컬럼: {list(trade_df.columns)}")
    except Exception as e:
        print(f"  ✗ 실패: {e}")
        print("\n  ※ '유가증권 일별매매정보' 서비스 이용 신청이 필요합니다.")
        sys.exit(1)

    # 3. 시총 상위 200 추출
    print("\n[3/3] KOSPI 시총 상위 200개 추출...")
    try:
        kospi200 = get_kospi200_stocks()
    except Exception as e:
        print(f"  ✗ 실패: {e}")
        sys.exit(1)

    # 결과 출력
    print("\n" + "=" * 60)
    print(f"최종 결과: {len(kospi200)}개 종목")
    print("=" * 60)

    display_df = kospi200.copy()
    display_df["시총_조원"] = (display_df["marcap"] / 1e12).round(2)
    display_df = display_df[["stock_code", "stock_name", "시총_조원"]]

    print("\n--- 상위 30개 ---")
    print(display_df.head(30).to_string(index=False))

    print("\n--- 하위 10개 (200위 부근) ---")
    print(display_df.tail(10).to_string(index=False))

    # CSV 저장
    output = Path("kospi200_list.csv")
    kospi200.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\n저장: {output.resolve()}")

    print("\n" + "=" * 60)
    if len(kospi200) == 200:
        print("✓ 정상 추출 완료. 다음 단계 진행 가능")
        print("  python test_dart_pdf.py")
    else:
        print(f"⚠ {len(kospi200)}개 추출됨 (기대 200)")
    print("=" * 60)


if __name__ == "__main__":
    main()

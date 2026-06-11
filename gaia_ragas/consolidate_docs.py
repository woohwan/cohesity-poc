"""
3개 데이터셋을 회사 중심 단일 디렉터리로 통합한다.
모든 파일(XML / PDF / XLS)을 원본 그대로 복사한다.
※ 변환 없음 — Cohesity GAIA가 비정형 문서를 얼마나 잘 처리하는지 테스트하는 것이 목적

출력 구조:
  dart_unified/
    {회사코드}_{회사명}/
      {날짜}_{보고서유형명}_{고유번호}.xml    ← 원본 복사
      {날짜}_{보고서유형명}_{고유번호}.pdf    ← 원본 복사
      {날짜}_{보고서유형명}_{고유번호}.xls    ← 원본 복사 (변환 없음)
      _index.json                            ← 회사별 파일 목록
    _master_index.csv                        ← 전체 인덱스

명명 규칙:
  날짜     : YYYYMMDD (filing_date)
  유형명   : 사업보고서 / 공정위공시 등 (REPORT_TYPE_MAP)
  고유번호 : receipt_no 뒤 6자리 (날짜 중복 제거)

실행:
  .venv/bin/python consolidate_docs.py               # 전체 실행
  .venv/bin/python consolidate_docs.py --dry-run     # 복사 없이 통계만 출력
  .venv/bin/python consolidate_docs.py --workers 4   # 병렬 처리
"""
import argparse
import csv
import json
import re
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# ── 설정 ─────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = BASE_DIR / "gaia_dataset"

DATASET_DIRS = {
    "dataset1": BASE_DIR / "dataset1" / "dart_kospi200_documents",
    "dataset2": BASE_DIR / "dataset2" / "dart_kospi200_rag",
    "dataset3": BASE_DIR / "dataset3" / "dart_kospi200_rag",
}

REPORT_TYPE_MAP = {
    "A": "사업보고서",
    "B": "주요사항보고서",
    "C": "발행공시",
    "D": "지분공시",
    "E": "기타공시",
    "F": "외부감사",
    "G": "펀드공시",
    "H": "자산유동화",
    "I": "거래소공시",
    "J": "공정위공시",
}

# 처리 대상 확장자 (원본 그대로 복사)
COPY_EXTENSIONS = {".xml", ".pdf", ".xls", ".xlsx"}


# ── 메타데이터 추출 ────────────────────────────────────────────────────────────

def parse_filing_dir(filing_dir: Path) -> Optional[dict]:
    """
    공시 폴더에서 메타데이터를 파싱한다.
    경로: .../{코드_회사명}/{유형}/{날짜_접수번호}/
    """
    parts = filing_dir.parts
    meta = {"company": "", "company_code": "", "report_type": "",
            "filing_date": "", "receipt_no": "", "dataset": ""}

    for part in parts:
        if part.startswith("dataset"):
            meta["dataset"] = part
            break

    for i, part in enumerate(parts):
        if re.match(r"^\d{6}_", part):
            meta["company_code"] = part[:6]
            meta["company"]      = part[7:]
            if i + 1 < len(parts):
                meta["report_type"] = parts[i + 1]
            if i + 2 < len(parts):
                dr = parts[i + 2]
                if "_" in dr:
                    meta["filing_date"] = dr[:8]
                    meta["receipt_no"]  = dr[9:]
            break

    if not meta["company_code"]:
        return None
    return meta


# ── 파일명 생성 ───────────────────────────────────────────────────────────────

def make_stem(meta: dict) -> str:
    """
    통합 파일 기본 이름(확장자 제외) 생성.
    예: 고려아연_20210831_공정위공시_000496
    """
    company   = meta["company"]
    date      = meta["filing_date"]
    type_name = REPORT_TYPE_MAP.get(meta["report_type"], meta["report_type"])
    # 접수번호 뒤 6자리 (날짜 8자리 제외)
    uid = meta["receipt_no"][-6:] if len(meta["receipt_no"]) >= 6 else meta["receipt_no"]
    return f"{company}_{date}_{type_name}_{uid}"


# ── 파일 처리 (단일 공시 폴더) ─────────────────────────────────────────────────

def process_filing(filing_dir: Path, dry_run: bool = False) -> list[dict]:
    """
    공시 폴더의 XML / PDF / XLS를 통합 디렉터리로 원본 그대로 복사한다.
    반환: 처리된 파일별 레코드 리스트 (마스터 인덱스용)
    """
    meta = parse_filing_dir(filing_dir)
    if not meta:
        return []

    company_dir = OUTPUT_DIR / f"{meta['company_code']}_{meta['company']}"
    stem        = make_stem(meta)
    records     = []

    for src in filing_dir.iterdir():
        if src.name.startswith("_"):
            continue

        ext = src.suffix.lower()
        if ext not in COPY_EXTENSIONS:
            continue

        dst    = company_dir / f"{stem}{ext}"
        record = _copy_file(src, dst, meta, ext.lstrip("."), dry_run)
        if record:
            records.append(record)

    return records


def _copy_file(src: Path, dst: Path, meta: dict, src_type: str,
               dry_run: bool) -> Optional[dict]:
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
    return _make_record(dst, meta, src_type, src.stat().st_size if src.exists() else 0)


def _make_record(dst: Path, meta: dict, src_type: str, size: int) -> dict:
    return {
        "file_path":    str(dst),
        "file_name":    dst.name,
        "company_code": meta["company_code"],
        "company":      meta["company"],
        "report_type":  meta["report_type"],
        "report_name":  REPORT_TYPE_MAP.get(meta["report_type"], meta["report_type"]),
        "filing_date":  meta["filing_date"],
        "receipt_no":   meta["receipt_no"],
        "dataset":      meta["dataset"],
        "source_type":  src_type,
        "size_bytes":   size,
    }


# ── 인덱스 저장 ───────────────────────────────────────────────────────────────

def save_master_index(records: list[dict]) -> Path:
    path = OUTPUT_DIR / "_master_index.csv"
    if not records:
        return path
    fieldnames = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"[저장] 마스터 인덱스 ({len(records):,}개) → {path}")
    return path


def save_company_index(records: list[dict]) -> None:
    """회사별 _index.json 저장."""
    from collections import defaultdict
    by_company: dict = defaultdict(list)
    for r in records:
        key = f"{r['company_code']}_{r['company']}"
        by_company[key].append({k: v for k, v in r.items()
                                 if k not in ("company_code", "company")})

    for company_key, items in by_company.items():
        idx_path = OUTPUT_DIR / company_key / "_index.json"
        if idx_path.parent.exists():
            with open(idx_path, "w", encoding="utf-8") as f:
                json.dump({"company": company_key,
                           "total": len(items),
                           "files": items}, f, ensure_ascii=False, indent=2)


def print_stats(records: list[dict]) -> None:
    from collections import Counter
    print("\n========== 통합 결과 ==========")
    print(f"총 파일 수 : {len(records):,}")
    type_cnt = Counter(r["source_type"] for r in records)
    for t, c in sorted(type_cnt.items(), key=lambda x: -x[1]):
        print(f"  {t:10s}: {c:,}")
    ds_cnt = Counter(r["dataset"] for r in records)
    print("데이터셋별:", dict(ds_cnt))
    companies = len(set(r["company_code"] for r in records))
    print(f"회사 수    : {companies:,}")
    total_mb = sum(r["size_bytes"] for r in records) / 1e6
    print(f"총 크기    : {total_mb:,.0f} MB")
    print("================================\n")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def collect_all_filing_dirs() -> list[Path]:
    dirs = []
    for ds_dir in DATASET_DIRS.values():
        if not ds_dir.exists():
            continue
        for company_dir in ds_dir.iterdir():
            if not company_dir.is_dir() or company_dir.name.startswith("_"):
                continue
            for type_dir in company_dir.iterdir():
                if not type_dir.is_dir():
                    continue
                for filing_dir in type_dir.iterdir():
                    if filing_dir.is_dir():
                        dirs.append(filing_dir)
    return dirs


def run(dry_run: bool = False, workers: int = 1) -> list[dict]:
    print("=" * 55)
    print("  DART 문서 통합 빌더")
    print("=" * 55)
    if dry_run:
        print("  [DRY-RUN] 실제 파일 복사 없음")
    print(f"  출력 경로: {OUTPUT_DIR}\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    filing_dirs = collect_all_filing_dirs()
    print(f"총 {len(filing_dirs):,}개 공시 폴더 처리 중...")

    all_records: list[dict] = []

    if workers > 1:
        # 멀티프로세스
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_filing, d, dry_run): d for d in filing_dirs}
            for fut in tqdm(as_completed(futs), total=len(filing_dirs), unit="filing"):
                all_records.extend(fut.result())
    else:
        for d in tqdm(filing_dirs, unit="filing"):
            all_records.extend(process_filing(d, dry_run))

    if not dry_run:
        save_master_index(all_records)
        save_company_index(all_records)

    print_stats(all_records)
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 문서 통합 빌더")
    parser.add_argument("--dry-run",  action="store_true", help="복사 없이 통계만 출력")
    parser.add_argument("--workers",  type=int, default=4, help="병렬 프로세스 수 (기본: 4)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, workers=args.workers)

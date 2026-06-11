"""
XML / PDF / XLS 파일을 개별 문서 단위로 무작위 샘플링.

샘플링 소스 (우선순위):
  1. GAIA_DATASET_DIR (통합 데이터셋) — 컨테이너 및 기본 모드
  2. DATASET_DIRS (dataset1/2/3) — 로컬 fallback
"""
import random
import json
from pathlib import Path
from typing import Optional
from collections import Counter

from tqdm import tqdm

from config import (
    GAIA_DATASET_DIR, DATASET_DIRS, SAMPLE_SIZE, MIN_TEXT_LENGTH,
    MAX_TEXT_LENGTH, RANDOM_SEED, OUTPUT_DIR, REPORT_TYPE_MAP,
)
from dart_xml_parser import parse_file, extract_metadata, SUPPORTED_EXTENSIONS

# 보고서 유형명 → 코드 역매핑 (gaia_dataset 파일명 파싱용)
_TYPE_NAME_TO_CODE = {v: k for k, v in REPORT_TYPE_MAP.items()}


def _extract_meta_from_gaia_path(file_path: Path) -> dict:
    """
    gaia_dataset 파일명에서 메타데이터 추출.
    경로: gaia_dataset/{코드_회사명}/{회사명}_{날짜}_{유형명}_{uid}.{ext}
    """
    parent = file_path.parent.name          # "000005_삼성전자"
    stem   = file_path.stem                 # "삼성전자_20210101_사업보고서_000496"

    company_code = parent[:6] if len(parent) >= 7 else ""
    company      = parent[7:] if len(parent) >= 7 else parent

    # 회사명에 _가 포함될 수 있으므로 오른쪽에서 3번 분리
    parts = stem.rsplit("_", 3)
    filing_date  = parts[1] if len(parts) >= 4 else ""
    report_name  = parts[2] if len(parts) >= 4 else ""
    uid          = parts[3] if len(parts) >= 4 else ""

    return {
        "company_code": company_code,
        "company":      company,
        "report_type":  _TYPE_NAME_TO_CODE.get(report_name, ""),
        "report_name":  report_name,
        "filing_date":  filing_date,
        "receipt_no":   uid,
        "dataset":      "gaia_dataset",
    }


def collect_all_files(source_dir: Path, company_filter: str = None) -> list[Path]:
    """디렉터리에서 XML/PDF/XLS 파일 수집. company_filter 지정 시 해당 회사만."""
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(source_dir.rglob(f"*{ext}"))
    files = [f for f in files if not f.name.startswith("_")]
    if company_filter:
        files = [f for f in files if company_filter in str(f)]
    return files


def _use_gaia_dataset() -> bool:
    """gaia_dataset 사용 여부 결정 (존재하면 우선 사용)."""
    return GAIA_DATASET_DIR.exists()


def _get_meta(file_path: Path) -> dict:
    """파일 경로에 맞는 메타데이터 추출 함수 선택."""
    if GAIA_DATASET_DIR in file_path.parents:
        return _extract_meta_from_gaia_path(file_path)
    return extract_metadata(file_path)


def sample_documents(
    sample_size: int = SAMPLE_SIZE,
    seed: int = RANDOM_SEED,
    min_length: int = MIN_TEXT_LENGTH,
    max_length: int = MAX_TEXT_LENGTH,
    company_filter: str = None,
) -> list[dict]:
    """
    문서 샘플링. gaia_dataset 존재 시 우선 사용, 없으면 dataset1/2/3 fallback.
    company_filter 지정 시 해당 회사 문서만 수집.
    """
    rng = random.Random(seed)
    results = []

    if _use_gaia_dataset():
        # gaia_dataset 모드 (컨테이너 및 통합 데이터 사용 시)
        all_files = collect_all_files(GAIA_DATASET_DIR, company_filter=company_filter)
        rng.shuffle(all_files)
        label = f"gaia_dataset{f'/{company_filter}' if company_filter else ''}"
        print(f"\n[{label}] {len(all_files):,}개 파일에서 최대 {sample_size}개 샘플링...")
    else:
        # 원본 dataset1/2/3 모드 (로컬 fallback)
        all_files = []
        for ds_name, ds_dir in DATASET_DIRS.items():
            if not ds_dir.exists():
                print(f"[WARN] {ds_dir} 없음. 건너뜁니다.")
                continue
            all_files.extend(collect_all_files(ds_dir, company_filter=company_filter))
        rng.shuffle(all_files)
        print(f"\n[dataset1/2/3] {len(all_files):,}개 파일에서 최대 {sample_size}개 샘플링...")

    type_counter: Counter = Counter()
    for file_path in tqdm(all_files, desc="샘플링", unit="file"):
        if len(results) >= sample_size:
            break
        text, source_type = parse_file(file_path)
        if text is None or len(text) < min_length:
            continue
        meta = _get_meta(file_path)
        type_counter[source_type] += 1
        results.append({
            **meta,
            "file_path":   str(file_path),
            "file_name":   file_path.name,
            "source_type": source_type,
            "text":        text[:max_length],
            "text_length": len(text),
        })

    rng.shuffle(results)
    print(f"  → {len(results)}개 수집 " +
          ", ".join(f"{t.upper()}: {c}" for t, c in sorted(type_counter.items())))
    return results


def save_sampled_documents(docs: list[dict], path: Optional[Path] = None) -> Path:
    if path is None:
        path = OUTPUT_DIR / "sampled_documents.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print(f"[저장] 샘플 문서 → {path}")
    return path


def load_sampled_documents(path: Optional[Path] = None) -> list[dict]:
    if path is None:
        path = OUTPUT_DIR / "sampled_documents.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_sample_stats(docs: list[dict]) -> None:
    counter: Counter = Counter(d["source_type"] for d in docs)
    ds_counter: Counter = Counter(d["dataset"] for d in docs)
    print("\n===== 샘플 문서 통계 =====")
    print(f"총 {len(docs)}개")
    print("타입별:", dict(counter))
    print("데이터셋별:", dict(ds_counter))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", type=str, default=None, help="특정 회사명 필터")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    args = parser.parse_args()

    docs = sample_documents(sample_size=args.sample_size, company_filter=args.company)
    suffix = f"_{args.company}" if args.company else ""
    path = OUTPUT_DIR / f"sampled_documents{suffix}.json"
    save_sampled_documents(docs, path)
    print_sample_stats(docs)

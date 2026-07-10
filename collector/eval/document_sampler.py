"""
gaia_test_200g_kr80_no_ocr 에서 문서 타입(pdf/docx_doc/xlsx_xls_csv/ppt_pptx)별
또는 수집 소스(=토픽, 예: dart_financial/bok_publications)별로 개별 문서를
무작위 샘플링한다.
"""
import random
import json
from pathlib import Path
from typing import Optional
from collections import Counter

from tqdm import tqdm

from config import (
    DATASET_DIR, TYPE_GROUPS, EXT_TO_GROUP, SAMPLE_SIZE_PER_TYPE,
    SAMPLE_SIZE_PER_SOURCE, MIN_TEXT_LENGTH, MAX_TEXT_LENGTH, RANDOM_SEED, OUTPUT_DIR,
)
from parsers import extract_text

# collector 내부 북키핑 파일 — 문서 취급하지 않고 제외
_EXCLUDE_NAMES = {"manifest.csv", "visited_pages.txt"}


def collect_files_by_type(type_group: str) -> list[Path]:
    """DATASET_DIR 전체를 훑어 특정 타입 그룹에 속하는 파일 목록을 반환."""
    exts = TYPE_GROUPS[type_group]
    files = []
    for ext in exts:
        for f in DATASET_DIR.rglob(f"*.{ext}"):
            if f.name in _EXCLUDE_NAMES or f.name.startswith("."):
                continue
            if "manifest.csv.bak" in f.name:
                continue
            files.append(f)
    return files


def _source_name(file_path: Path) -> str:
    """DATASET_DIR 바로 아래 소스 디렉터리 이름 (예: bok_publications)."""
    try:
        rel = file_path.relative_to(DATASET_DIR)
        return rel.parts[0] if rel.parts else ""
    except ValueError:
        return ""


def list_sources() -> list[str]:
    """DATASET_DIR 바로 아래에 있는 소스(=토픽) 디렉터리 이름 목록."""
    return sorted(p.name for p in DATASET_DIR.iterdir() if p.is_dir())


def collect_files_by_source(source: str) -> list[Path]:
    """특정 소스 디렉터리 아래에서 대상 확장자(TYPE_GROUPS에 속한 것)의 파일 목록을 반환."""
    source_dir = DATASET_DIR / source
    if not source_dir.is_dir():
        return []
    files = []
    for ext in EXT_TO_GROUP:
        for f in source_dir.rglob(f"*.{ext}"):
            if f.name in _EXCLUDE_NAMES or f.name.startswith("."):
                continue
            if "manifest.csv.bak" in f.name:
                continue
            files.append(f)
    return files


def sample_documents_for_type(
    type_group: str,
    sample_size: int = SAMPLE_SIZE_PER_TYPE,
    seed: int = RANDOM_SEED,
    min_length: int = MIN_TEXT_LENGTH,
    max_length: int = MAX_TEXT_LENGTH,
) -> list[dict]:
    rng = random.Random(seed)
    all_files = collect_files_by_type(type_group)
    rng.shuffle(all_files)
    print(f"\n[{type_group}] {len(all_files):,}개 파일에서 최대 {sample_size}개 샘플링...")

    results = []
    ext_counter: Counter = Counter()
    pbar = tqdm(all_files, desc=type_group, unit="file")
    for file_path in pbar:
        if len(results) >= sample_size:
            break
        text = extract_text(file_path)
        if not text or len(text) < min_length:
            continue
        ext = file_path.suffix.lower().lstrip(".")
        ext_counter[ext] += 1
        results.append({
            "doc_id":      file_path.stem[:64] + "_" + str(abs(hash(str(file_path))))[:8],
            "file_path":   str(file_path),
            "file_name":   file_path.name,
            "source":      _source_name(file_path),
            "type_group":  type_group,
            "ext":         ext,
            "text":        text[:max_length],
            "text_length": len(text),
        })
        pbar.set_postfix(found=len(results))

    print(f"  → {len(results)}개 수집 (확장자별: {dict(ext_counter)})")
    return results


def sample_documents_for_source(
    source: str,
    sample_size: int = SAMPLE_SIZE_PER_SOURCE,
    seed: int = RANDOM_SEED,
    min_length: int = MIN_TEXT_LENGTH,
    max_length: int = MAX_TEXT_LENGTH,
) -> list[dict]:
    """특정 소스(=토픽) 디렉터리에서 문서 타입에 관계없이 무작위 샘플링한다."""
    rng = random.Random(seed)
    all_files = collect_files_by_source(source)
    rng.shuffle(all_files)
    print(f"\n[{source}] {len(all_files):,}개 파일에서 최대 {sample_size}개 샘플링...")

    results = []
    ext_counter: Counter = Counter()
    pbar = tqdm(all_files, desc=source, unit="file")
    for file_path in pbar:
        if len(results) >= sample_size:
            break
        text = extract_text(file_path)
        if not text or len(text) < min_length:
            continue
        ext = file_path.suffix.lower().lstrip(".")
        ext_counter[ext] += 1
        results.append({
            "doc_id":      file_path.stem[:64] + "_" + str(abs(hash(str(file_path))))[:8],
            "file_path":   str(file_path),
            "file_name":   file_path.name,
            "source":      source,
            "type_group":  EXT_TO_GROUP.get(ext, ext),
            "ext":         ext,
            "text":        text[:max_length],
            "text_length": len(text),
        })
        pbar.set_postfix(found=len(results))

    print(f"  → {len(results)}개 수집 (확장자별: {dict(ext_counter)})")
    return results


def save_sampled_documents(docs: list[dict], group_key: str) -> Path:
    """group_key: 타입명(pdf 등) 또는 소스명(bok_publications 등) — 출력 파일명에 쓰인다."""
    path = OUTPUT_DIR / f"sampled_documents_{group_key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print(f"[저장] 샘플 문서 → {path}")
    return path


def load_sampled_documents(group_key: str) -> list[dict]:
    path = OUTPUT_DIR / f"sampled_documents_{group_key}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-by", choices=["type", "source"], default="type",
                         help="type(문서 타입) 또는 source(수집 소스=토픽) 단위로 샘플링")
    parser.add_argument("--type", choices=list(TYPE_GROUPS.keys()), default=None,
                         help="[--group-by type] 특정 타입만 샘플링 (미지정 시 전체 타입)")
    parser.add_argument("--source", default=None,
                         help="[--group-by source] 특정 소스만 샘플링 (미지정 시 전체 소스)")
    parser.add_argument("--sample-size", type=int, default=None)
    args = parser.parse_args()

    if args.group_by == "type":
        keys = [args.type] if args.type else list(TYPE_GROUPS.keys())
        sample_size = args.sample_size or SAMPLE_SIZE_PER_TYPE
        sampler = sample_documents_for_type
    else:
        keys = [args.source] if args.source else list_sources()
        sample_size = args.sample_size or SAMPLE_SIZE_PER_SOURCE
        sampler = sample_documents_for_source

    for key in keys:
        docs = sampler(key, sample_size=sample_size)
        save_sampled_documents(docs, key)

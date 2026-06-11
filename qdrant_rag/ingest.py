"""
gaia_dataset/ 의 XML/PDF/XLS 파일을 파싱 → 청킹 → 임베딩 → Qdrant 저장

사용법:
  python ingest.py                  # 전체 인제스트
  python ingest.py --limit 500      # 테스트용 (파일 500개만)
  python ingest.py --reset          # 컬렉션 초기화 후 재인제스트
  python ingest.py --company 삼성전자 # 특정 회사만
"""
import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# config는 sys.path 조작 전에 import (gaia_ragas/config.py 가 가려지는 것 방지)
from config import (
    GAIA_DATASET_DIR, QDRANT_PATH, QDRANT_URL,
    COLLECTION_NAME, EMBED_MODEL, EMBED_DIMENSION, EMBED_BATCH_SIZE,
    CHUNK_SIZE, CHUNK_OVERLAP, MIN_CHUNK_LENGTH,
    CHECKPOINT_FILE,
)

# 컨테이너: dart_xml_parser.py 가 /app 에 복사됨 → 직접 import
# 로컬: gaia_ragas/ 에서 import
try:
    from dart_xml_parser import parse_file
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "gaia_ragas"))
    from dart_xml_parser import parse_file  # noqa: E402

SUPPORTED_EXT = {".xml", ".pdf", ".xls", ".xlsx"}


# ── Qdrant 초기화 ─────────────────────────────────────────────────────────────

def get_client():
    from qdrant_client import QdrantClient
    if QDRANT_URL:
        return QdrantClient(url=QDRANT_URL)
    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(QDRANT_PATH))


def ensure_collection(client, reset: bool = False):
    from qdrant_client.models import Distance, VectorParams
    exists = any(c.name == COLLECTION_NAME
                 for c in client.get_collections().collections)
    if exists and reset:
        client.delete_collection(COLLECTION_NAME)
        exists = False
    if not exists:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBED_DIMENSION, distance=Distance.COSINE),
        )
        print(f"[Qdrant] 컬렉션 생성: {COLLECTION_NAME}")
    else:
        cnt = client.count(COLLECTION_NAME).count
        print(f"[Qdrant] 기존 컬렉션 사용: {COLLECTION_NAME} (벡터 {cnt:,}개)")


# ── 체크포인트 ────────────────────────────────────────────────────────────────

def load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_checkpoint(done: set) -> None:
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


# ── 파일 목록 수집 ─────────────────────────────────────────────────────────────

def collect_files(company_filter: Optional[str] = None) -> list[Path]:
    files = []
    if not GAIA_DATASET_DIR.exists():
        print(f"[ERROR] gaia_dataset 디렉터리가 없습니다: {GAIA_DATASET_DIR}")
        sys.exit(1)
    for company_dir in sorted(GAIA_DATASET_DIR.iterdir()):
        if not company_dir.is_dir() or company_dir.name.startswith("_"):
            continue
        if company_filter and company_filter not in company_dir.name:
            continue
        for f in company_dir.iterdir():
            if f.suffix.lower() in SUPPORTED_EXT and not f.name.startswith("_"):
                files.append(f)
    return files


# ── 텍스트 청킹 ───────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", ".", " ", ""],
    )
    chunks = splitter.split_text(text)
    return [c for c in chunks if len(c) >= MIN_CHUNK_LENGTH]


# ── 메타데이터 파싱 (파일명에서 추출) ─────────────────────────────────────────

def extract_meta_from_path(file_path: Path) -> dict:
    """
    파일 경로에서 메타데이터 추출.
    company_dir: {코드}_{회사명}
    file_name:   {회사명}_{날짜}_{보고서유형}_{uid}.{ext}
    """
    company_dir = file_path.parent.name       # 000030_우리금융지주
    stem        = file_path.stem              # 우리금융지주_20210101_사업보고서_000496
    parts       = stem.split("_")

    meta = {
        "company_code": company_dir[:6] if len(company_dir) >= 7 else "",
        "company":      company_dir[7:] if len(company_dir) >= 7 else company_dir,
        "filing_date":  parts[1] if len(parts) >= 2 else "",
        "report_name":  parts[2] if len(parts) >= 3 else "",
        "uid":          parts[3] if len(parts) >= 4 else "",
        "source_type":  file_path.suffix.lstrip(".").lower(),
        "file_name":    file_path.name,
    }
    return meta


# ── 임베딩 모델 로드 ──────────────────────────────────────────────────────────

_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"[임베딩] 모델 로딩: {EMBED_MODEL}")
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def embed_batch(texts: list[str]) -> list[list[float]]:
    model = get_embed_model()
    vecs = model.encode(texts, batch_size=EMBED_BATCH_SIZE,
                        show_progress_bar=False, normalize_embeddings=True)
    return vecs.tolist()


# ── Qdrant 업서트 ─────────────────────────────────────────────────────────────

def upsert_chunks(client, file_path: Path, chunks: list[str], meta: dict) -> int:
    from qdrant_client.models import PointStruct

    vectors = embed_batch(chunks)
    points = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        payload = {**meta, "chunk_index": i, "text": chunk}
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload=payload,
        ))

    # 100개 단위 배치 업서트
    BATCH = 100
    for start in range(0, len(points), BATCH):
        client.upsert(collection_name=COLLECTION_NAME,
                      points=points[start:start + BATCH])
    return len(points)


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(limit: Optional[int] = None, reset: bool = False,
        company_filter: Optional[str] = None) -> None:
    client = get_client()
    ensure_collection(client, reset=reset)

    done = set() if reset else load_checkpoint()
    all_files = collect_files(company_filter)

    pending = [f for f in all_files if str(f) not in done]
    if limit:
        pending = pending[:limit]

    print(f"\n전체 파일: {len(all_files):,}  |  처리 완료: {len(done):,}  |  처리 예정: {len(pending):,}\n")

    total_chunks = 0
    errors = 0

    for file_path in tqdm(pending, unit="file", desc="인제스트"):
        try:
            text, _ = parse_file(file_path)
            if not text or len(text) < MIN_CHUNK_LENGTH:
                done.add(str(file_path))
                continue

            chunks = chunk_text(text)
            if not chunks:
                done.add(str(file_path))
                continue

            meta = extract_meta_from_path(file_path)
            n = upsert_chunks(client, file_path, chunks, meta)
            total_chunks += n
            done.add(str(file_path))

        except Exception as e:
            tqdm.write(f"[ERROR] {file_path.name}: {e}")
            errors += 1

        # 100파일마다 체크포인트 저장
        if len(done) % 100 == 0:
            save_checkpoint(done)

    save_checkpoint(done)

    total_vec = client.count(COLLECTION_NAME).count
    print(f"\n=== 인제스트 완료 ===")
    print(f"  처리 파일  : {len(pending) - errors:,}  (오류: {errors})")
    print(f"  추가 청크  : {total_chunks:,}")
    print(f"  총 벡터 수 : {total_vec:,}")
    print(f"  저장 경로  : {QDRANT_PATH if not QDRANT_URL else QDRANT_URL}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 문서 → Qdrant 인제스트")
    parser.add_argument("--limit",   type=int,   default=None, help="처리 파일 수 제한 (테스트용)")
    parser.add_argument("--reset",   action="store_true",      help="컬렉션 초기화 후 재인제스트")
    parser.add_argument("--company", type=str,   default=None, help="특정 회사명 필터")
    args = parser.parse_args()
    run(limit=args.limit, reset=args.reset, company_filter=args.company)

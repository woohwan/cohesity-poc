"""
gaia_dataset/ 의 XML/PDF/XLS 파일을 파싱 → 청킹 → 임베딩 → Qdrant 저장

사용법:
  python ingest.py                          # 전체 인제스트
  python ingest.py --limit 500              # 테스트용 (파일 500개만)
  python ingest.py --reset                  # 컬렉션 초기화 후 재인제스트
  python ingest.py --company 삼성전자        # 특정 회사만
  python ingest.py --workers 8              # 파싱 8스레드 병렬
  python ingest.py --gpus 2,3,4,5          # 멀티 GPU (각 GPU = 별도 프로세스)
  python ingest.py --gpus 2,3 --workers 8  # 멀티 GPU + 파싱 병렬
"""
import argparse
import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from config import (
    GAIA_DATASET_DIR, QDRANT_PATH, QDRANT_URL,
    COLLECTION_NAME, EMBED_MODEL, EMBED_DIMENSION, EMBED_BATCH_SIZE,
    CHUNK_SIZE, CHUNK_OVERLAP, MIN_CHUNK_LENGTH,
    CHUNK_SEPARATORS, CHUNK_KEEP_SEPARATOR, CHUNK_SEPARATOR_REGEX,
    CHECKPOINT_FILE, LOG_FILE, LOG_INTERVAL, HF_TOKEN,
    USE_HYBRID_SEARCH, BM25_MODEL,
)

try:
    from dart_xml_parser import parse_file
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "gaia_ragas"))
    from dart_xml_parser import parse_file  # noqa: E402

SUPPORTED_EXT    = {".xml", ".pdf", ".xls", ".xlsx"}
FILE_BUFFER_SIZE = 200   # 한 번에 GPU에 보낼 파일 수


# ── 파일 로거 ─────────────────────────────────────────────────────────────────

class _FileLogger:
    """tqdm과 별개로 진행 상황을 output/ingest.log 에 기록."""

    def __init__(self, total: int, gpus: Optional[list] = None):
        self.total      = total
        self.gpus       = gpus or []
        self.start_time = time.time()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, msg: str) -> None:
        ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)

    def log_start(self, workers: int) -> None:
        self._write("=" * 70)
        self._write(f"인제스트 시작  |  모델: {EMBED_MODEL}")
        self._write(
            f"GPU: {','.join(self.gpus) if self.gpus else 'CPU/단일'}  "
            f"|  파싱 워커: {workers}  |  처리 예정: {self.total:,}개 파일"
        )
        self._write(f"로그 파일: {LOG_FILE}")

    def log_progress(self, n: int, gpu_counts: dict, errors: int) -> None:
        elapsed   = time.time() - self.start_time
        rate      = n / elapsed if elapsed > 0 else 0
        remaining = (self.total - n) / rate if rate > 0 else 0
        pct       = n / self.total * 100 if self.total > 0 else 0
        el_str    = str(datetime.timedelta(seconds=int(elapsed)))
        eta_str   = str(datetime.timedelta(seconds=int(remaining)))
        gpu_str   = "  ".join(f"G{g}:{c:,}" for g, c in gpu_counts.items())
        self._write(
            f"진행: {n:,}/{self.total:,} ({pct:.1f}%)  "
            f"|  경과: {el_str}  |  남은시간: ~{eta_str}  "
            f"|  {rate:.1f} file/s  |  오류: {errors}"
            + (f"  |  {gpu_str}" if gpu_str else "")
        )

    def log_error(self, filename: str, msg: str) -> None:
        self._write(f"[ERROR] {filename}: {msg}")

    def log_done(self, n_ok: int, errors: int, total_chunks: int, total_vec: int) -> None:
        elapsed = time.time() - self.start_time
        el_str  = str(datetime.timedelta(seconds=int(elapsed)))
        self._write(
            f"인제스트 완료  |  처리: {n_ok:,}개 (오류: {errors})  "
            f"|  청크: {total_chunks:,}  |  총 벡터: {total_vec:,}  |  소요: {el_str}"
        )
        self._write("=" * 70)


# ── Qdrant 초기화 ─────────────────────────────────────────────────────────────

def get_client():
    from qdrant_client import QdrantClient
    if QDRANT_URL:
        return QdrantClient(url=QDRANT_URL)
    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(QDRANT_PATH))


def ensure_collection(client, reset: bool = False):
    from qdrant_client.models import (
        Distance, VectorParams, SparseVectorParams, SparseIndexParams,
    )
    exists = any(c.name == COLLECTION_NAME
                 for c in client.get_collections().collections)
    if exists and reset:
        client.delete_collection(COLLECTION_NAME)
        exists = False
    if not exists:
        if USE_HYBRID_SEARCH:
            # dense + sparse(BM25) 하이브리드 컬렉션
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={"dense": VectorParams(size=EMBED_DIMENSION, distance=Distance.COSINE)},
                sparse_vectors_config={"bm25": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False)
                )},
            )
            print(f"[Qdrant] 컬렉션 생성 (하이브리드): {COLLECTION_NAME}")
        else:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBED_DIMENSION, distance=Distance.COSINE),
            )
            print(f"[Qdrant] 컬렉션 생성: {COLLECTION_NAME}")
    else:
        cnt = client.count(COLLECTION_NAME).count
        print(f"[Qdrant] 기존 컬렉션 사용: {COLLECTION_NAME} (벡터 {cnt:,}개)")


# ── 체크포인트 ────────────────────────────────────────────────────────────────

def _shard_ckpt_path(gpu_id: str) -> Path:
    return CHECKPOINT_FILE.with_name(f"ingest_checkpoint_gpu{gpu_id}.json")


def load_checkpoint() -> set:
    done: set = set()
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            done.update(json.load(f))
    for shard_file in CHECKPOINT_FILE.parent.glob("ingest_checkpoint_gpu*.json"):
        with open(shard_file, encoding="utf-8") as f:
            done.update(json.load(f))
    return done


def save_checkpoint(done: set, gpu_id: Optional[str] = None) -> None:
    path = _shard_ckpt_path(gpu_id) if gpu_id else CHECKPOINT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def clear_checkpoint() -> None:
    targets = [CHECKPOINT_FILE] + list(
        CHECKPOINT_FILE.parent.glob("ingest_checkpoint_gpu*.json")
    )
    for f in targets:
        if f.exists():
            f.unlink()
            print(f"[초기화] 체크포인트 삭제: {f}")


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
        separators=CHUNK_SEPARATORS,
        keep_separator=CHUNK_KEEP_SEPARATOR,
        is_separator_regex=CHUNK_SEPARATOR_REGEX,
    )
    chunks = splitter.split_text(text)
    return [c for c in chunks if len(c) >= MIN_CHUNK_LENGTH]


# ── 메타데이터 파싱 ───────────────────────────────────────────────────────────

def extract_meta_from_path(file_path: Path) -> dict:
    company_dir = file_path.parent.name
    stem        = file_path.stem
    parts = stem.rsplit("_", 3)
    return {
        "company_code": company_dir[:6] if len(company_dir) >= 7 else "",
        "company":      company_dir[7:] if len(company_dir) >= 7 else company_dir,
        "filing_date":  int(parts[1]) if len(parts) >= 4 and parts[1].isdigit() else 0,
        "report_name":  parts[2] if len(parts) >= 4 else "",
        "uid":          parts[3] if len(parts) >= 4 else "",
        "source_type":  file_path.suffix.lstrip(".").lower(),
        "file_name":    file_path.name,
    }


# ── 임베딩 모델 로드 ──────────────────────────────────────────────────────────

_embed_model = None
_bm25_model  = None


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[임베딩] 모델 로딩: {EMBED_MODEL}  (device: {device})", flush=True)
        if device == "cuda":
            print(f"[임베딩] GPU: {torch.cuda.get_device_name(0)}  "
                  f"메모리: {torch.cuda.get_device_properties(0).total_memory // 1024**3}GB",
                  flush=True)
        kwargs = {"token": HF_TOKEN} if HF_TOKEN else {}
        _embed_model = SentenceTransformer(EMBED_MODEL, device=device, **kwargs)
    return _embed_model


def get_bm25_model():
    global _bm25_model
    if _bm25_model is None:
        from fastembed import SparseTextEmbedding
        print(f"[BM25] 모델 로딩: {BM25_MODEL}", flush=True)
        _bm25_model = SparseTextEmbedding(model_name=BM25_MODEL)
    return _bm25_model


# ── Qdrant 업서트 (다중 파일 배치) ────────────────────────────────────────────

def embed_and_upsert_batch(client, buffer: list[tuple]) -> int:
    from qdrant_client.models import PointStruct, SparseVector

    all_chunks: list[str] = []
    offsets: list[int] = [0]
    for _, chunks, _ in buffer:
        all_chunks.extend(chunks)
        offsets.append(len(all_chunks))

    if not all_chunks:
        return 0

    # Dense 임베딩 (GPU)
    dense_model  = get_embed_model()
    dense_vecs   = dense_model.encode(
        all_chunks,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    # BM25 Sparse 임베딩 (하이브리드 모드일 때만)
    sparse_vecs = None
    if USE_HYBRID_SEARCH:
        bm25 = get_bm25_model()
        sparse_vecs = list(bm25.embed(all_chunks))

    total = 0
    UPSERT_BATCH = 100
    for i, (file_path, chunks, meta) in enumerate(buffer):
        s, e = offsets[i], offsets[i + 1]
        if s == e:
            continue

        points = []
        for j in range(s, e):
            payload = {**meta, "chunk_index": j - s, "text": chunks[j - s]}
            if USE_HYBRID_SEARCH and sparse_vecs is not None:
                sv = sparse_vecs[j]
                vector = {
                    "dense": dense_vecs[j].tolist(),
                    "bm25": SparseVector(
                        indices=sv.indices.tolist(),
                        values=sv.values.tolist(),
                    ),
                }
            else:
                vector = dense_vecs[j].tolist()
            points.append(PointStruct(id=str(uuid.uuid4()), vector=vector, payload=payload))

        for b in range(0, len(points), UPSERT_BATCH):
            client.upsert(collection_name=COLLECTION_NAME,
                          points=points[b:b + UPSERT_BATCH])
        total += len(points)

    return total


# ── 파일 파싱 ────────────────────────────────────────────────────────────────

def parse_one(file_path: Path) -> tuple:
    try:
        text, _ = parse_file(file_path)
        if not text or len(text) < MIN_CHUNK_LENGTH:
            return file_path, [], extract_meta_from_path(file_path)
        chunks = chunk_text(text)
        meta = extract_meta_from_path(file_path)
        return file_path, chunks, meta
    except Exception as e:
        return file_path, None, str(e)


# ── 인제스트 루프 ─────────────────────────────────────────────────────────────

def _ingest_loop(client, pending: list[Path], done: set,
                 workers: int, gpu_id: Optional[str] = None,
                 progress_queue=None,
                 logger: Optional[_FileLogger] = None) -> tuple[int, int]:
    """
    pending 파일을 파싱 → 임베딩 → 업서트.
    progress_queue가 있으면 파일 처리마다 ('FILE', gpu_id) 메시지를 전송 (멀티 GPU).
    logger가 있으면 단일 GPU 모드에서 LOG_INTERVAL마다 파일에 기록.
    반환: (total_chunks, errors)
    """
    from concurrent.futures import ThreadPoolExecutor

    total_chunks = 0
    errors       = 0
    n_processed  = 0
    buffer: list[tuple] = []
    label = f"GPU {gpu_id}" if gpu_id else "인제스트"

    def flush_buffer():
        nonlocal total_chunks
        if not buffer:
            return
        n = embed_and_upsert_batch(client, buffer)
        total_chunks += n
        for fp, _, _ in buffer:
            done.add(str(fp))
        buffer.clear()
        save_checkpoint(done, gpu_id)

    def process_result(result: tuple):
        nonlocal errors, n_processed
        file_path, chunks, meta_or_err = result
        if chunks is None:
            print(f"[{label}][ERROR] {file_path.name}: {meta_or_err}", flush=True)
            if logger:
                logger.log_error(file_path.name, meta_or_err)
            errors += 1
        elif not chunks:
            done.add(str(file_path))
        else:
            buffer.append((file_path, chunks, meta_or_err))
            if len(buffer) >= FILE_BUFFER_SIZE:
                flush_buffer()

        n_processed += 1

        # 멀티 GPU: 부모 프로세스에 진행 보고
        if progress_queue is not None:
            progress_queue.put(("FILE", gpu_id))

        # 단일 GPU: 로그 파일에 주기적 기록
        if logger and n_processed % LOG_INTERVAL == 0:
            logger.log_progress(n_processed, {}, errors)

    # 멀티 GPU 모드: tqdm 없이 실행 (부모가 통합 표시)
    # 단일 GPU 모드: tqdm으로 직접 표시
    use_tqdm = progress_queue is None

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            it = executor.map(parse_one, pending)
            if use_tqdm:
                it = tqdm(it, total=len(pending), unit="file", desc=label)
            for result in it:
                process_result(result)
    else:
        it = pending
        if use_tqdm:
            it = tqdm(it, unit="file", desc=label)
        for file_path in it:
            process_result(parse_one(file_path))

    flush_buffer()
    return total_chunks, errors


# ── 멀티 GPU 워커 (spawn된 자식 프로세스 진입점) ──────────────────────────────

def _shard_worker(gpu_id: str, file_paths_str: list[str], workers: int,
                  progress_queue) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id  # torch import 전에 설정

    from pathlib import Path as _Path

    client = get_client()
    file_paths = [_Path(p) for p in file_paths_str]

    shard_done: set = set()
    shard_ckpt = _shard_ckpt_path(gpu_id)
    if shard_ckpt.exists():
        with open(shard_ckpt, encoding="utf-8") as f:
            shard_done = set(json.load(f))

    pending = [f for f in file_paths if str(f) not in shard_done]
    print(f"[GPU {gpu_id}] 시작: {len(pending):,}개 파일", flush=True)

    total_chunks, errors = _ingest_loop(
        client, pending, shard_done, workers, gpu_id, progress_queue
    )

    progress_queue.put(("DONE", gpu_id, total_chunks, errors))
    print(f"[GPU {gpu_id}] 완료: 청크 {total_chunks:,}개, 오류 {errors}개", flush=True)


# ── 멀티 GPU 오케스트레이터 ───────────────────────────────────────────────────

def _run_multi_gpu(gpus: list[str], limit: Optional[int], reset: bool,
                   company_filter: Optional[str], workers: int) -> None:
    import multiprocessing as mp

    if reset:
        clear_checkpoint()
    done = load_checkpoint()
    all_files = collect_files(company_filter)
    pending = [f for f in all_files if str(f) not in done]
    if limit:
        pending = pending[:limit]

    n = len(gpus)
    shards = [pending[i::n] for i in range(n)]
    total_pending = len(pending)

    print(f"\n전체 파일: {len(all_files):,}  |  처리 완료: {len(done):,}  |  처리 예정: {total_pending:,}")
    for gpu_id, shard in zip(gpus, shards):
        print(f"  GPU {gpu_id}: {len(shard):,}개 파일")
    print()

    # 컬렉션 생성은 spawn 전 부모에서 한 번만 (워커는 기존 컬렉션 사용)
    _client = get_client()
    ensure_collection(_client, reset=reset)

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()

    procs = [
        ctx.Process(target=_shard_worker,
                    args=(gpu_id, [str(f) for f in shard], workers, queue))
        for gpu_id, shard in zip(gpus, shards)
    ]
    for p in procs:
        p.start()

    # ── 통합 progress bar + 로그 기록 (부모 프로세스) ──────────────────────────
    logger           = _FileLogger(total_pending, gpus)
    gpu_counts       = {g: 0 for g in gpus}
    done_gpus        = 0
    n_processed      = 0
    total_chunks_all = 0
    errors_all       = 0

    logger.log_start(workers)
    print(f"진행 로그: {LOG_FILE}", flush=True)

    bar_fmt = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} 파일 [{elapsed}<{remaining}, {rate_fmt}]"

    try:
        with tqdm(total=total_pending, unit="file", desc="전체 진행",
                  bar_format=bar_fmt, dynamic_ncols=True) as pbar:
            while done_gpus < len(gpus):
                try:
                    msg = queue.get(timeout=120)
                except Exception:
                    if all(not p.is_alive() for p in procs):
                        break
                    continue

                if msg[0] == "FILE":
                    _, gpu_id = msg
                    gpu_counts[gpu_id] += 1
                    n_processed += 1
                    pbar.update(1)
                    pbar.set_postfix(
                        {f"G{g}": c for g, c in gpu_counts.items()},
                        refresh=False,
                    )
                    if n_processed % LOG_INTERVAL == 0:
                        logger.log_progress(n_processed, gpu_counts, errors_all)

                elif msg[0] == "DONE":
                    _, gpu_id, chunks, errors = msg
                    done_gpus    += 1
                    total_chunks_all += chunks
                    errors_all   += errors

    except KeyboardInterrupt:
        print("\n[인터럽트] 워커 종료 중...", flush=True)
        logger.log_progress(n_processed, gpu_counts, errors_all)
        for p in procs:
            p.terminate()

    for p in procs:
        p.join()

    failed = [gpus[i] for i, p in enumerate(procs) if p.exitcode not in (0, None)]
    if failed:
        print(f"[경고] GPU {failed} 워커가 비정상 종료했습니다.")

    client = get_client()
    total_vec = client.count(COLLECTION_NAME).count
    n_ok = n_processed - errors_all

    logger.log_done(n_ok, errors_all, total_chunks_all, total_vec)

    print(f"\n=== 멀티 GPU 인제스트 완료 (GPU: {gpus}) ===")
    print(f"  처리 파일  : {n_ok:,}  (오류: {errors_all})")
    print(f"  추가 청크  : {total_chunks_all:,}")
    print(f"  총 벡터 수 : {total_vec:,}")
    print(f"  저장 경로  : {QDRANT_PATH if not QDRANT_URL else QDRANT_URL}")
    print(f"  진행 로그  : {LOG_FILE}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(limit: Optional[int] = None, reset: bool = False,
        company_filter: Optional[str] = None, workers: int = 1,
        gpus: Optional[list[str]] = None) -> None:

    if gpus and len(gpus) > 1:
        _run_multi_gpu(gpus, limit, reset, company_filter, workers)
        return

    client = get_client()
    ensure_collection(client, reset=reset)

    if reset:
        clear_checkpoint()
    done = load_checkpoint()
    all_files = collect_files(company_filter)

    pending = [f for f in all_files if str(f) not in done]
    if limit:
        pending = pending[:limit]

    print(f"\n전체 파일: {len(all_files):,}  |  처리 완료: {len(done):,}  |  처리 예정: {len(pending):,}")
    if workers > 1:
        print(f"파싱 병렬 처리: {workers}개 스레드  |  임베딩 배치: {FILE_BUFFER_SIZE}파일 단위")
    print(f"진행 로그: {LOG_FILE}")
    print()

    logger = _FileLogger(len(pending))
    logger.log_start(workers)

    total_chunks, errors = _ingest_loop(client, pending, done, workers, logger=logger)

    total_vec = client.count(COLLECTION_NAME).count
    logger.log_done(len(pending) - errors, errors, total_chunks, total_vec)

    print(f"\n=== 인제스트 완료 ===")
    print(f"  처리 파일  : {len(pending) - errors:,}  (오류: {errors})")
    print(f"  추가 청크  : {total_chunks:,}")
    print(f"  총 벡터 수 : {total_vec:,}")
    print(f"  저장 경로  : {QDRANT_PATH if not QDRANT_URL else QDRANT_URL}")
    print(f"  진행 로그  : {LOG_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 문서 → Qdrant 인제스트")
    parser.add_argument("--limit",   type=int,   default=None,
                        help="처리 파일 수 제한 (테스트용)")
    parser.add_argument("--reset",   action="store_true",
                        help="컬렉션 초기화 후 재인제스트")
    parser.add_argument("--company", type=str,   default=None,
                        help="특정 회사명 필터")
    parser.add_argument("--workers", type=int,   default=1,
                        help="파일 파싱 병렬 스레드 수 (기본: 1)")
    parser.add_argument("--gpus",    type=str,   default=None,
                        help="멀티 GPU 번호, 쉼표 구분 (예: '2,3,4,5')")
    args = parser.parse_args()

    gpus = [g.strip() for g in args.gpus.split(",")] if args.gpus else None
    run(limit=args.limit, reset=args.reset, company_filter=args.company,
        workers=args.workers, gpus=gpus)

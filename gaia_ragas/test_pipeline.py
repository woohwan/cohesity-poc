"""
파이프라인 단계별 검증 스크립트

사용법:
  # 1단계: API 없이 파서/샘플러만 검증 (빠름, 무료)
  python test_pipeline.py --stage parse

  # 2단계: Claude API로 QA 소규모 생성 (5문서 × 2QA = 10개)
  python test_pipeline.py --stage qa

  # 3단계: RAGAS 테스트셋 포맷 검증
  python test_pipeline.py --stage testset

  # 전체 mini 파이프라인 한번에
  python test_pipeline.py --stage all
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── 색상 출력 헬퍼 ────────────────────────────────────────────────────────────
OK   = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m→\033[0m"

def ok(msg):   print(f"  {OK}  {msg}")
def fail(msg): print(f"  {FAIL}  {msg}")
def info(msg): print(f"  {INFO}  {msg}")


# ── Stage 1: 파서 / 샘플러 검증 ───────────────────────────────────────────────

def test_parsers():
    """XML / PDF / XLS 파서 각각 동작 확인."""
    print("\n[Stage 1] 파서 검증")
    from config import DATASET_DIRS, MIN_TEXT_LENGTH
    from dart_xml_parser import parse_file, extract_metadata
    import random

    rng = random.Random(42)
    results = {"xml": None, "pdf": None, "xls": None}

    for ds_name, ds_dir in DATASET_DIRS.items():
        if not ds_dir.exists():
            fail(f"{ds_dir} 없음")
            continue

        for ext in [".xml", ".pdf", ".xls"]:
            if results.get(ext.lstrip(".")) is not None:
                continue
            candidates = [f for f in ds_dir.rglob(f"*{ext}") if not f.name.startswith("_")]
            rng.shuffle(candidates)
            for f in candidates[:20]:
                text, src = parse_file(f)
                if text and len(text) >= MIN_TEXT_LENGTH:
                    meta = extract_metadata(f)
                    results[src] = {
                        "file": f.name[:50],
                        "company": meta["company"],
                        "length": len(text),
                        "preview": text[:80].replace("\n", " "),
                    }
                    break

    all_ok = True
    for src_type in ["xml", "pdf", "xls"]:
        r = results.get(src_type)
        if r:
            ok(f"{src_type.upper():3s} | {r['company']:10s} | {r['length']:,}자 | {r['preview']}")
        else:
            fail(f"{src_type.upper()} 파싱 실패 또는 샘플 없음")
            all_ok = False

    return all_ok


def test_sampler(n: int = 9):
    """document_sampler: 각 dataset에서 3개씩 샘플링."""
    print(f"\n[Stage 1] 샘플러 검증 (dataset당 3개, 총 {n}개 목표)")
    from document_sampler import sample_documents

    docs = sample_documents(sample_size=n, seed=42)

    from collections import Counter
    type_cnt = Counter(d["source_type"] for d in docs)
    ds_cnt   = Counter(d["dataset"]      for d in docs)

    if len(docs) == 0:
        fail("수집된 문서 없음")
        return False

    ok(f"총 {len(docs)}개 수집")
    info(f"타입별: {dict(type_cnt)}")
    info(f"데이터셋별: {dict(ds_cnt)}")

    for d in docs[:3]:
        info(f"  {d['source_type'].upper():3s} | {d['company']:10s} | {d['report_type']} | {d['text_length']:,}자")

    return len(docs) > 0


# ── Stage 2: QA 생성 검증 ────────────────────────────────────────────────────

def test_qa_generation(n_docs: int = 5, n_qa: int = 2):
    """Claude API로 소규모 QA 생성 (API 키 필요)."""
    print(f"\n[Stage 2] QA 생성 검증 ({n_docs}문서 × {n_qa}QA)")

    from config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        fail("ANTHROPIC_API_KEY 환경 변수가 없습니다.")
        info("export ANTHROPIC_API_KEY=sk-ant-...")
        return False

    # 소규모 샘플
    from document_sampler import sample_documents
    docs = sample_documents(sample_size=n_docs, seed=99)
    if not docs:
        fail("샘플 문서 없음")
        return False

    from qa_generator import generate_all_qa
    from config import OUTPUT_DIR

    out_path = OUTPUT_DIR / "test_qa_pairs.json"
    qa_pairs = generate_all_qa(docs, n_qa_per_doc=n_qa, output_path=out_path)

    if not qa_pairs:
        fail("QA 생성 결과 없음")
        return False

    ok(f"{len(qa_pairs)}개 QA 생성 완료 → {out_path}")

    from collections import Counter
    type_cnt = Counter(q.get("question_type", "?") for q in qa_pairs)
    info(f"질문 유형: {dict(type_cnt)}")

    for qa in qa_pairs[:2]:
        info(f"  Q: {qa['question'][:60]}")
        info(f"  A: {qa['answer'][:60]}")
        print()

    return True


# ── Stage 3: RAGAS 테스트셋 포맷 검증 ────────────────────────────────────────

def test_testset_format():
    """qa_pairs.json → ragas_testset.json / gaia_eval.csv 변환 확인."""
    print("\n[Stage 3] RAGAS 테스트셋 포맷 검증")

    from config import OUTPUT_DIR

    # test_qa_pairs.json 또는 qa_pairs.json 사용
    qa_path = OUTPUT_DIR / "test_qa_pairs.json"
    if not qa_path.exists():
        qa_path = OUTPUT_DIR / "qa_pairs.json"
    if not qa_path.exists():
        fail("qa_pairs.json 없음 — Stage 2 먼저 실행하세요")
        return False

    from ragas_testset_creator import load_qa_pairs, build_ragas_samples, save_ragas_json, save_gaia_eval_csv

    qa_pairs = load_qa_pairs(qa_path)
    samples  = build_ragas_samples(qa_pairs)

    test_ragas = OUTPUT_DIR / "test_ragas_testset.json"
    test_csv   = OUTPUT_DIR / "test_gaia_eval.csv"
    save_ragas_json(samples, test_ragas)
    save_gaia_eval_csv(samples, test_csv)

    # 필수 필드 검증
    required = {"user_input", "reference", "retrieved_contexts", "response"}
    missing = required - set(samples[0].keys())
    if missing:
        fail(f"필수 필드 누락: {missing}")
        return False

    ok(f"RAGAS JSON: {len(samples)}개 샘플 → {test_ragas}")
    ok(f"GAIA CSV:   {len(samples)}행 → {test_csv}")

    # CSV 미리보기
    import csv
    with open(test_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    info(f"CSV 샘플 행: no={row['no']} | {row['company']} | {row['source_type']} | Q: {row['question'][:40]}")

    # RAGAS 패키지 import 확인
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy
        ok("ragas 패키지 정상 import")
    except ImportError as e:
        fail(f"ragas import 실패: {e}")
        info("pip install ragas 필요")

    return True


# ── Stage 4: GAIA API 연결 확인 ───────────────────────────────────────────────

def test_gaia_connection():
    """GAIA 클러스터 연결 가능 여부만 확인 (실제 쿼리 X)."""
    print("\n[Stage 4] GAIA API 연결 확인")
    import os, requests

    url   = os.environ.get("COHESITY_CLUSTER_URL", "")
    token = os.environ.get("COHESITY_API_TOKEN", "")

    if not url or not token:
        fail("COHESITY_CLUSTER_URL / COHESITY_API_TOKEN 미설정")
        info("export COHESITY_CLUSTER_URL=https://<cluster-ip>")
        info("export COHESITY_API_TOKEN=<bearer-token>")
        return False

    try:
        resp = requests.get(
            f"{url}/irisservices/api/v1/public/basicClusterInfo",
            headers={"Authorization": f"Bearer {token}"},
            verify=False, timeout=10,
        )
        if resp.ok:
            cluster = resp.json()
            ok(f"클러스터 연결 성공: {cluster.get('name', url)}")
            return True
        else:
            fail(f"HTTP {resp.status_code}: {resp.text[:100]}")
            return False
    except Exception as e:
        fail(f"연결 실패: {e}")
        return False


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DART GAIA/RAGAS 파이프라인 단계별 검증")
    parser.add_argument(
        "--stage",
        choices=["parse", "qa", "testset", "gaia", "all"],
        default="parse",
        help="검증할 단계 (기본: parse)",
    )
    args = parser.parse_args()

    print("=" * 55)
    print("  DART GAIA/RAGAS 파이프라인 테스트")
    print("=" * 55)

    results = {}

    if args.stage in ("parse", "all"):
        results["parse"]   = test_parsers()
        results["sampler"] = test_sampler()

    if args.stage in ("qa", "all"):
        results["qa"] = test_qa_generation()

    if args.stage in ("testset", "all"):
        results["testset"] = test_testset_format()

    if args.stage == "gaia":
        results["gaia"] = test_gaia_connection()

    # 결과 요약
    print("\n" + "=" * 55)
    print("  결과 요약")
    print("=" * 55)
    for name, passed in results.items():
        status = f"\033[92mPASS\033[0m" if passed else f"\033[91mFAIL\033[0m"
        print(f"  {status}  {name}")

    failed = [k for k, v in results.items() if not v]
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

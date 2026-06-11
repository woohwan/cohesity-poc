"""
전체 파이프라인 실행:
  1. 문서 샘플링  (document_sampler)
  2. QA 쌍 생성  (qa_generator)
  3. RAGAS 테스트셋 생성 (ragas_testset_creator)
  4. [선택] GAIA 평가 (gaia_evaluator)

사용법:
  # A안: 전체 랜덤 샘플링
  python run_pipeline.py
  python run_pipeline.py --step sample
  python run_pipeline.py --step qa
  python run_pipeline.py --step testset

  # B안: 특정 회사만 (인제스트와 일치시킬 때)
  python run_pipeline.py --company 삼성전자
  python run_pipeline.py --step sample --company 삼성전자
  python run_pipeline.py --step qa    --company 삼성전자

  # 출력 파일:
  #   (A안) qa_pairs.json
  #   (B안) qa_pairs_삼성전자.json
"""
import argparse
import sys
from pathlib import Path

from config import OUTPUT_DIR, SAMPLE_SIZE, QA_PER_DOC


def _file_suffix(args) -> str:
    return f"_{args.company}" if getattr(args, "company", None) else ""


def step_sample(args) -> None:
    from document_sampler import sample_documents, save_sampled_documents
    docs = sample_documents(sample_size=args.sample_size,
                            company_filter=getattr(args, "company", None))
    path = OUTPUT_DIR / f"sampled_documents{_file_suffix(args)}.json"
    save_sampled_documents(docs, path)
    print(f"\n[1/3] 문서 샘플링 완료: {len(docs)}개 → {path.name}")


def step_qa(args) -> None:
    from document_sampler import load_sampled_documents
    from qa_generator import generate_all_qa

    suffix = _file_suffix(args)
    sampled_path = OUTPUT_DIR / f"sampled_documents{suffix}.json"
    if not sampled_path.exists():
        print(f"[ERROR] {sampled_path.name}이 없습니다. --step sample을 먼저 실행하세요.")
        sys.exit(1)

    docs = load_sampled_documents(sampled_path)
    qa_path = OUTPUT_DIR / f"qa_pairs{suffix}.json"
    qa_pairs = generate_all_qa(docs, n_qa_per_doc=args.qa_per_doc, output_path=qa_path)
    print(f"\n[2/3] QA 생성 완료: {len(qa_pairs)}개 → {qa_path.name}")


def step_testset(args) -> None:
    from ragas_testset_creator import create_testset

    suffix = _file_suffix(args)
    qa_path = OUTPUT_DIR / f"qa_pairs{suffix}.json"
    if not qa_path.exists():
        print(f"[ERROR] {qa_path.name}이 없습니다. --step qa를 먼저 실행하세요.")
        sys.exit(1)

    samples = create_testset(qa_path)
    print(f"\n[3/3] RAGAS 테스트셋 생성 완료: {len(samples)}개")


def step_evaluate(args) -> None:
    from gaia_evaluator import run_gaia_evaluation, run_ragas_evaluation

    print("\n[4/4] GAIA 평가 시작...")
    run_gaia_evaluation()
    run_ragas_evaluation()


def main():
    parser = argparse.ArgumentParser(description="DART GAIA/RAGAS 테스트 파이프라인")
    parser.add_argument(
        "--step",
        choices=["sample", "qa", "testset", "evaluate", "all"],
        default="all",
        help="실행할 단계 (기본: all)",
    )
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE,
                        help=f"샘플 문서 수 (기본: {SAMPLE_SIZE})")
    parser.add_argument("--qa-per-doc",  type=int, default=QA_PER_DOC,
                        help=f"문서당 QA 수 (기본: {QA_PER_DOC})")
    parser.add_argument("--company",     type=str, default=None,
                        help="특정 회사명 필터 (B안: 회사별 QA 생성)")
    args = parser.parse_args()

    print("=" * 55)
    print("  DART KOSPI200 GAIA/RAGAS 테스트 파이프라인")
    print("=" * 55)
    print(f"출력 디렉터리: {OUTPUT_DIR}")
    if args.company:
        print(f"회사 필터    : {args.company}")
    print()

    step_map = {
        "sample":   [step_sample],
        "qa":       [step_qa],
        "testset":  [step_testset],
        "evaluate": [step_evaluate],
        "all":      [step_sample, step_qa, step_testset],
    }

    for step_fn in step_map[args.step]:
        step_fn(args)

    suffix = _file_suffix(args)
    print("\n파이프라인 완료.")
    print(f"결과 파일 위치: {OUTPUT_DIR}")
    print(f"  - sampled_documents{suffix}.json : 샘플링된 문서")
    print(f"  - qa_pairs{suffix}.json          : 생성된 QA 쌍")
    print(f"  - ragas_testset.json             : RAGAS 테스트셋")
    print(f"  - gaia_eval.csv                  : GAIA 평가용 CSV")


if __name__ == "__main__":
    main()

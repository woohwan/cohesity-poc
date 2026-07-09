"""
문서 타입별(pdf/docx_doc/xlsx_xls_csv/ppt_pptx) QA/RAGAS 데이터셋 생성 파이프라인.

Usage:
  python run_pipeline.py --step sample                 # 타입별 문서 샘플링
  python run_pipeline.py --step qa                      # 타입별 QA 쌍 생성 (LLM 호출)
  python run_pipeline.py --step testset                 # 타입별 RAGAS 테스트셋 변환
  python run_pipeline.py --step evaluate                # Cohesity GAIA API 평가 + RAGAS 채점
  python run_pipeline.py --step all                     # sample+qa+testset (evaluate는 별도 실행 권장)
  python run_pipeline.py --step all --type pdf          # 특정 타입만
"""
import argparse

from config import TYPE_GROUPS, SAMPLE_SIZE_PER_TYPE


def run_sample(types: list[str], sample_size: int) -> None:
    from document_sampler import sample_documents_for_type, save_sampled_documents
    for t in types:
        docs = sample_documents_for_type(t, sample_size=sample_size)
        save_sampled_documents(docs, t)


def run_qa(types: list[str]) -> None:
    from document_sampler import load_sampled_documents
    from qa_generator import generate_all_qa
    for t in types:
        docs = load_sampled_documents(t)
        generate_all_qa(docs, t)


def run_testset(types: list[str]) -> None:
    from ragas_testset_creator import create_testset
    for t in types:
        create_testset(t)


def run_evaluate(types: list[str], max_samples: int | None) -> None:
    from gaia_evaluator import run_gaia_evaluation, run_ragas_evaluation
    for t in types:
        run_gaia_evaluation(t, max_samples=max_samples)
        run_ragas_evaluation(t)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["sample", "qa", "testset", "evaluate", "all"], default="all")
    parser.add_argument("--type", choices=list(TYPE_GROUPS.keys()), default=None,
                         help="특정 타입만 실행 (미지정 시 전체 타입)")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE_PER_TYPE)
    parser.add_argument("--max-samples", type=int, default=None,
                         help="evaluate 단계에서 타입별로 처음 N개만 GAIA에 질의 (비용/시간 절약용)")
    args = parser.parse_args()

    types = [args.type] if args.type else list(TYPE_GROUPS.keys())

    if args.step in ("sample", "all"):
        run_sample(types, args.sample_size)
    if args.step in ("qa", "all"):
        run_qa(types)
    if args.step in ("testset", "all"):
        run_testset(types)
    if args.step == "evaluate":
        run_evaluate(types, args.max_samples)


if __name__ == "__main__":
    main()

"""
문서 타입별(pdf/docx_doc/xlsx_xls_csv/ppt_pptx) 또는 수집 소스별(=토픽,
예: dart_financial/bok_publications) QA/RAGAS 데이터셋 생성 파이프라인.

Usage:
  python run_pipeline.py --step sample                        # 타입별 문서 샘플링
  python run_pipeline.py --step qa                             # 타입별 QA 쌍 생성 (LLM 호출)
  python run_pipeline.py --step testset                        # 타입별 RAGAS 테스트셋 변환
  python run_pipeline.py --step evaluate                       # Cohesity GAIA API 평가 + RAGAS 채점
  python run_pipeline.py --step all                            # sample+qa+testset (evaluate는 별도 실행 권장)
  python run_pipeline.py --step all --type pdf                 # 특정 타입만

  # 소스(=토픽) 단위로 실행 — 예: manifest.csv의 source 컬럼(dart_financial 등) 기준
  python run_pipeline.py --step all --group-by source                    # 전체 소스
  python run_pipeline.py --step all --group-by source --source bok_publications  # 특정 소스만
"""
import argparse

from config import TYPE_GROUPS, SAMPLE_SIZE_PER_TYPE, SAMPLE_SIZE_PER_SOURCE


def run_sample(group_by: str, keys: list[str], sample_size: int) -> None:
    from document_sampler import (
        sample_documents_for_type, sample_documents_for_source, save_sampled_documents,
    )
    sampler = sample_documents_for_type if group_by == "type" else sample_documents_for_source
    for key in keys:
        docs = sampler(key, sample_size=sample_size)
        save_sampled_documents(docs, key)


def run_qa(keys: list[str]) -> None:
    from document_sampler import load_sampled_documents
    from qa_generator import generate_all_qa
    for key in keys:
        docs = load_sampled_documents(key)
        generate_all_qa(docs, key)


def run_testset(keys: list[str]) -> None:
    from ragas_testset_creator import create_testset
    for key in keys:
        create_testset(key)


def run_evaluate(keys: list[str], max_samples: int | None) -> None:
    from gaia_evaluator import run_gaia_evaluation, run_ragas_evaluation
    for key in keys:
        run_gaia_evaluation(key, max_samples=max_samples)
        run_ragas_evaluation(key)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["sample", "qa", "testset", "evaluate", "all"], default="all")
    parser.add_argument("--group-by", choices=["type", "source"], default="type",
                         help="type(문서 타입) 또는 source(수집 소스=토픽) 단위로 데이터셋 생성")
    parser.add_argument("--type", choices=list(TYPE_GROUPS.keys()), default=None,
                         help="[--group-by type] 특정 타입만 실행 (미지정 시 전체 타입)")
    parser.add_argument("--source", default=None,
                         help="[--group-by source] 특정 소스(토픽)만 실행 (미지정 시 전체 소스)")
    parser.add_argument("--sample-size", type=int, default=None,
                         help="그룹당 샘플 문서 수 (미지정 시 type=100, source=20 기본값 사용)")
    parser.add_argument("--max-samples", type=int, default=None,
                         help="evaluate 단계에서 그룹별로 처음 N개만 GAIA에 질의 (비용/시간 절약용)")
    args = parser.parse_args()

    if args.group_by == "type":
        if args.source:
            parser.error("--source 는 --group-by source 일 때만 사용합니다.")
        keys = [args.type] if args.type else list(TYPE_GROUPS.keys())
        sample_size = args.sample_size or SAMPLE_SIZE_PER_TYPE
    else:
        if args.type:
            parser.error("--type 은 --group-by type 일 때만 사용합니다.")
        from document_sampler import list_sources
        keys = [args.source] if args.source else list_sources()
        sample_size = args.sample_size or SAMPLE_SIZE_PER_SOURCE

    if args.step in ("sample", "all"):
        run_sample(args.group_by, keys, sample_size)
    if args.step in ("qa", "all"):
        run_qa(keys)
    if args.step in ("testset", "all"):
        run_testset(keys)
    if args.step == "evaluate":
        run_evaluate(keys, args.max_samples)


if __name__ == "__main__":
    main()

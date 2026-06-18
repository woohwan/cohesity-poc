"""
교보증권 / 삼성전자 / 현대자동차 혼합 200개 QA 데이터셋 생성 및 GAIA/RAGAS 평가 스크립트.

출력 디렉터리: gaia_ragas/eval/
  eval/qa_pairs_mixed.json     — 3개사 혼합 QA 쌍 (200개)
  eval/ragas_testset.json      — RAGAS 평가용 테스트셋
  eval/gaia_eval.csv           — GAIA 평가용 CSV
  eval/gaia_eval_results.csv   — GAIA API 응답 결과
  eval/ragas_eval_results.csv  — RAGAS 4개 메트릭 점수

사용법:
  python eval_pipeline.py                    # QA 생성 + 테스트셋 변환
  python eval_pipeline.py --step qa          # QA 생성만
  python eval_pipeline.py --step testset     # 테스트셋 변환만 (QA 파일 있을 때)
  python eval_pipeline.py --step evaluate    # GAIA + RAGAS 평가 (클러스터 필요)
  python eval_pipeline.py --step all         # 전체 (QA → 테스트셋 → 평가)
  python eval_pipeline.py --reset            # eval/ 초기화 후 재실행
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

# gaia_ragas 모듈 경로 추가
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

EVAL_DIR = Path(os.environ.get("EVAL_DIR", str(SCRIPT_DIR / "eval")))
EVAL_DIR.mkdir(parents=True, exist_ok=True)

# 평가 대상 3개사 및 목표 QA 수
COMPANIES = ["교보증권", "삼성전자", "현대자동차"]
TOTAL_QA   = 200
PER_COMPANY = TOTAL_QA // len(COMPANIES)          # 66
REMAINDER   = TOTAL_QA - PER_COMPANY * len(COMPANIES)  # 2

RANDOM_SEED  = 42
QA_PER_DOC   = 2


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _quota(idx: int) -> int:
    """회사별 목표 QA 수: 앞쪽 회사에 나머지를 1개씩 배분."""
    return PER_COMPANY + (1 if idx < REMAINDER else 0)


def _clear_eval_dir() -> None:
    targets = [
        "qa_pairs_mixed.json",
        "ragas_testset.json",
        "gaia_eval.csv",
        "gaia_eval_results.csv",
        "ragas_eval_results.csv",
        "sampled_documents_교보증권.json",
        "sampled_documents_삼성전자.json",
        "sampled_documents_현대자동차.json",
    ]
    for name in targets:
        p = EVAL_DIR / name
        if p.exists():
            p.unlink()
            print(f"[초기화] 삭제: {p.name}")


# ── Step 1: 회사별 QA 생성 ────────────────────────────────────────────────────

def _docs_needed(quota: int) -> int:
    """목표 QA 수를 채우기 위해 샘플링할 문서 수 (여유 30% 추가)."""
    return max(int((quota / QA_PER_DOC) * 1.3) + 1, 10)


def generate_qa_for_company(company: str, quota: int) -> list[dict]:
    """단일 회사에서 quota개 QA를 생성해 반환."""
    from document_sampler import sample_documents
    from qa_generator import generate_all_qa

    sample_path = EVAL_DIR / f"sampled_documents_{company}.json"
    qa_path     = EVAL_DIR / f"qa_pairs_{company}.json"

    print(f"\n{'='*55}")
    print(f"  [{company}] 목표 QA: {quota}개")
    print(f"{'='*55}")

    # 문서 샘플링
    docs = sample_documents(
        sample_size=_docs_needed(quota),
        seed=RANDOM_SEED,
        company_filter=company,
    )
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print(f"[저장] 샘플 문서 {len(docs)}개 → {sample_path.name}")

    # QA 생성
    qa_pairs = generate_all_qa(docs, n_qa_per_doc=QA_PER_DOC, output_path=qa_path)

    # 목표 수량으로 슬라이스 (초과 생성된 경우)
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(qa_pairs)
    qa_pairs = qa_pairs[:quota]

    print(f"[{company}] 최종 QA: {len(qa_pairs)}개")
    return qa_pairs


def step_qa() -> list[dict]:
    """3개사 QA 생성 후 혼합 → eval/qa_pairs_mixed.json 저장."""
    all_qa: list[dict] = []

    for idx, company in enumerate(COMPANIES):
        quota = _quota(idx)
        qa    = generate_qa_for_company(company, quota)
        all_qa.extend(qa)

    # 섞어서 저장
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(all_qa)

    out_path = EVAL_DIR / "qa_pairs_mixed.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, ensure_ascii=False, indent=2)

    print(f"\n[완료] 혼합 QA {len(all_qa)}개 저장 → {out_path}")
    _print_distribution(all_qa)
    return all_qa


def _print_distribution(qa_pairs: list[dict]) -> None:
    from collections import Counter
    dist = Counter(q.get("company", "?") for q in qa_pairs)
    print("\n[회사별 분포]")
    for company, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {company}: {cnt}개")


# ── Step 2: RAGAS 테스트셋 변환 ───────────────────────────────────────────────

def step_testset() -> list[dict]:
    """eval/qa_pairs_mixed.json → eval/ragas_testset.json + eval/gaia_eval.csv"""
    qa_path = EVAL_DIR / "qa_pairs_mixed.json"
    if not qa_path.exists():
        print(f"[ERROR] {qa_path} 없음. --step qa를 먼저 실행하세요.")
        sys.exit(1)

    from ragas_testset_creator import build_ragas_samples, save_ragas_json, save_gaia_eval_csv

    with open(qa_path, encoding="utf-8") as f:
        qa_pairs = json.load(f)

    samples = build_ragas_samples(qa_pairs)

    save_ragas_json(samples,      EVAL_DIR / "ragas_testset.json")
    save_gaia_eval_csv(samples,   EVAL_DIR / "gaia_eval.csv")

    print(f"\n[완료] RAGAS 테스트셋 {len(samples)}개 생성")
    return samples


# ── Step 3: GAIA API 평가 ────────────────────────────────────────────────────

def step_evaluate_gaia() -> None:
    """eval/ragas_testset.json 기반으로 GAIA API 호출 → eval/gaia_eval_results.csv"""
    from gaia_evaluator import CLUSTER_URL, API_KEY, query_gaia, extract_gaia_answer

    testset_path = EVAL_DIR / "ragas_testset.json"
    output_path  = EVAL_DIR / "gaia_eval_results.csv"

    if not testset_path.exists():
        print(f"[ERROR] {testset_path} 없음. --step testset을 먼저 실행하세요.")
        sys.exit(1)

    if not CLUSTER_URL or not API_KEY:
        print("[SKIP] COHESITY_CLUSTER_URL / COHESITY_API_KEY 미설정 — GAIA 평가를 건너뜁니다.")
        print("  export COHESITY_CLUSTER_URL=https://<helios-fqdn>")
        print("  export COHESITY_API_KEY=<api-key>")
        return

    import pandas as pd
    from tqdm import tqdm

    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)
    samples = testset.get("samples", testset)

    print(f"\n[GAIA 평가] {len(samples)}개 질문 처리 중...")
    rows = []
    for sample in tqdm(samples, unit="qa"):
        question = sample["user_input"]
        try:
            resp = query_gaia(question)
            answer, contexts = extract_gaia_answer(resp)
        except Exception as e:
            print(f"  [ERROR] {question[:40]}... → {e}")
            answer, contexts = "", []
            time.sleep(2)

        rows.append({
            "user_input":         question,
            "gaia_response":      answer,
            "retrieved_contexts": contexts,
            "reference":          sample["reference"],
            "company":            sample.get("company", ""),
            "source_type":        sample.get("source_type", ""),
        })
        time.sleep(0.3)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[저장] GAIA 응답 결과 → {output_path}")


# ── Step 4: RAGAS 메트릭 평가 ────────────────────────────────────────────────

def step_evaluate_ragas() -> None:
    """eval/gaia_eval_results.csv + eval/ragas_testset.json → RAGAS 4개 메트릭."""
    import math
    import pandas as pd

    gaia_csv     = EVAL_DIR / "gaia_eval_results.csv"
    testset_path = EVAL_DIR / "ragas_testset.json"
    output_path  = EVAL_DIR / "ragas_eval_results.csv"

    if not gaia_csv.exists():
        print(f"[SKIP] {gaia_csv} 없음 — GAIA 평가를 먼저 실행하세요.")
        return

    try:
        from ragas import evaluate
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall
        from llm_client import build_langchain_llm
    except ImportError as e:
        print(f"[SKIP] ragas 패키지 없음: {e}")
        return

    # LLM
    try:
        ragas_llm = LangchainLLMWrapper(build_langchain_llm())
    except Exception as e:
        print(f"[WARN] LLM 설정 실패: {e}")
        ragas_llm = None

    # Embeddings (OpenAI fallback 방지)
    ragas_embeddings = None
    try:
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from langchain_huggingface import HuggingFaceEmbeddings
        ragas_embeddings = LangchainEmbeddingsWrapper(
            HuggingFaceEmbeddings(
                model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )
        )
    except Exception as e:
        print(f"[WARN] embeddings 설정 실패: {e}")

    # 데이터 로드
    gaia_df = pd.read_csv(gaia_csv)
    with open(testset_path, encoding="utf-8") as f:
        testset = json.load(f)
    samples = testset.get("samples", testset)

    ragas_samples = []
    for row, sample in zip(gaia_df.itertuples(), samples):
        contexts = row.retrieved_contexts
        if isinstance(contexts, str):
            try:
                contexts = json.loads(contexts)
            except Exception:
                contexts = [contexts] if contexts else []
        ragas_samples.append(SingleTurnSample(
            user_input=sample["user_input"],
            response=row.gaia_response if row.gaia_response else "",
            retrieved_contexts=contexts,
            reference=sample["reference"],
        ))

    dataset = EvaluationDataset(ragas_samples)
    metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]

    for m in metrics:
        if ragas_llm and hasattr(m, "llm"):
            m.llm = ragas_llm
        if ragas_embeddings and hasattr(m, "embeddings"):
            m.embeddings = ragas_embeddings

    eval_kwargs = {"dataset": dataset, "metrics": metrics}
    if ragas_llm:
        eval_kwargs["llm"] = ragas_llm
    if ragas_embeddings:
        eval_kwargs["embeddings"] = ragas_embeddings

    print(f"\n[RAGAS 평가] {len(ragas_samples)}개 샘플 평가 중...")
    result_df = evaluate(**eval_kwargs).to_pandas()
    result_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[저장] RAGAS 결과 → {output_path}")

    # 요약 출력
    targets = {"faithfulness": 0.7, "answer_relevancy": 0.6,
               "context_precision": 0.5, "context_recall": 0.5}
    print("\n========== RAGAS 평가 결과 ==========")
    print(f"  평가 QA 수: {len(ragas_samples)}개")
    for metric, target in targets.items():
        if metric in result_df.columns:
            score = result_df[metric].mean()
            if math.isnan(score):
                continue
            bar  = "█" * int(score * 20)
            ok   = "PASS" if score >= target else "FAIL"
            print(f"  {metric:<25}: {score:.4f}  {bar}  (목표 {target}) → {ok}")
    print("=====================================\n")

    # 회사별 점수
    if "company" in gaia_df.columns:
        print("[회사별 RAGAS 점수]")
        result_df["company"] = gaia_df["company"].values
        for company in COMPANIES:
            sub = result_df[result_df["company"].str.contains(company, na=False)]
            if sub.empty:
                continue
            scores = {m: sub[m].mean() for m in targets if m in sub.columns}
            scores_str = "  ".join(f"{k}: {v:.3f}" for k, v in scores.items() if not math.isnan(v))
            print(f"  {company}: {scores_str}")


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="3개사 혼합 200개 QA 생성 및 GAIA/RAGAS 평가")
    parser.add_argument(
        "--step",
        choices=["qa", "testset", "evaluate", "all"],
        default="qa",
        help="실행 단계 (기본: qa)",
    )
    parser.add_argument("--reset", action="store_true", help="eval/ 초기화 후 재실행")
    args = parser.parse_args()

    print("=" * 55)
    print("  혼합 QA 200개 생성 및 GAIA/RAGAS 평가 파이프라인")
    print(f"  대상: {', '.join(COMPANIES)}")
    print(f"  출력: {EVAL_DIR}")
    print("=" * 55)

    if args.reset:
        _clear_eval_dir()

    if args.step in ("qa", "all"):
        step_qa()

    if args.step in ("testset", "all"):
        step_testset()

    if args.step in ("evaluate", "all"):
        step_evaluate_gaia()
        step_evaluate_ragas()

    print(f"\n완료. 결과 위치: {EVAL_DIR}")


if __name__ == "__main__":
    main()

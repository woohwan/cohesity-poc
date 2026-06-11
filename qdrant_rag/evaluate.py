"""
RAGAS로 Qdrant RAG 파이프라인 품질 평가

사전 조건:
  - gaia_ragas/output/qa_pairs.json 가 생성되어 있어야 함
  - Qdrant 컬렉션에 문서가 인제스트되어 있어야 함
  - ANTHROPIC_API_KEY 설정

사용법:
  python evaluate.py
  python evaluate.py --limit 20        # QA 20개만
  python evaluate.py --out my_eval.csv # 결과 파일명 지정
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from config import GAIA_RAGAS_DIR, OUTPUT_DIR, TOP_K
from qa_chain import ask

def _resolve_qa_path(company_filter: str = None) -> Path:
    """
    회사 필터가 있으면 qa_pairs_{company}.json 우선 탐색.
    없으면 qa_pairs.json 사용.
    """
    output_dir = GAIA_RAGAS_DIR / "output"
    if company_filter:
        company_path = output_dir / f"qa_pairs_{company_filter}.json"
        if company_path.exists():
            print(f"[로드] 회사별 QA 파일 사용: {company_path.name}")
            return company_path
        print(f"[INFO] {company_path.name} 없음 → qa_pairs.json 에서 필터링")
    return output_dir / "qa_pairs.json"


def load_qa_pairs(limit: int = None, company_filter: str = None) -> list[dict]:
    qa_path = _resolve_qa_path(company_filter)
    if not qa_path.exists():
        print(f"[ERROR] QA 파일이 없습니다: {qa_path}")
        print("  gaia_ragas/ 에서 먼저 실행하세요:")
        print("    ./run.sh --step sample [--company 회사명]")
        print("    ./run.sh --step qa     [--company 회사명]")
        sys.exit(1)
    with open(qa_path, encoding="utf-8") as f:
        pairs = json.load(f)
    if company_filter and not qa_path.name.startswith(f"qa_pairs_{company_filter}"):
        pairs = [p for p in pairs if company_filter in p.get("company", "")]
        print(f"[필터] 회사명 '{company_filter}' 적용 → {len(pairs):,}개")
    if limit:
        pairs = pairs[:limit]
    print(f"[로드] QA 쌍: {len(pairs):,}개")
    return pairs


def run_eval(qa_pairs: list[dict], company_filter: str = None) -> list[dict]:
    """각 QA 쌍에 대해 RAG 실행 후 RAGAS 입력 형식으로 변환."""
    from tqdm import tqdm
    rows = []
    for qa in tqdm(qa_pairs, desc="RAG 실행", unit="qa"):
        question  = qa.get("question", "")
        reference = qa.get("answer",   "")
        company   = company_filter or qa.get("company", None)

        try:
            result = ask(question, top_k=TOP_K, company_filter=company)
            rows.append({
                "user_input":         question,
                "reference":          reference,
                "response":           result["answer"],
                "retrieved_contexts": result["contexts"],
                "company":            qa.get("company", ""),
                "source_type":        qa.get("source_type", ""),
                "doc_id":             qa.get("doc_id", ""),
            })
        except Exception as e:
            print(f"[ERROR] {question[:40]}... → {e}")
    return rows


def compute_ragas(rows: list[dict]) -> dict:
    """RAGAS 메트릭 계산."""
    try:
        from ragas import evaluate
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
        from ragas.metrics import faithfulness, answer_relevancy
        from langchain_anthropic import ChatAnthropic
        from ragas.llms import LangchainLLMWrapper

        from config import CLAUDE_MODEL, ANTHROPIC_API_KEY
        llm = ChatAnthropic(model=CLAUDE_MODEL, api_key=ANTHROPIC_API_KEY)
        ragas_llm = LangchainLLMWrapper(llm)

        samples = []
        for r in rows:
            samples.append(SingleTurnSample(
                user_input=r["user_input"],
                reference=r["reference"],
                response=r["response"],
                retrieved_contexts=r["retrieved_contexts"],
            ))

        dataset = EvaluationDataset(samples=samples)
        metrics = [faithfulness, answer_relevancy]

        # LLM 주입
        for m in metrics:
            if hasattr(m, "llm"):
                m.llm = ragas_llm

        result = evaluate(dataset=dataset, metrics=metrics)
        return result.to_pandas().mean(numeric_only=True).to_dict()

    except Exception as e:
        print(f"[RAGAS] 메트릭 계산 실패: {e}")
        return {}


def save_results(rows: list[dict], scores: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # CSV 저장
    csv_path = out_path.with_suffix(".csv")
    fieldnames = ["user_input", "response", "reference",
                  "company", "source_type", "doc_id",
                  "contexts_count"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "user_input":     r["user_input"],
                "response":       r["response"],
                "reference":      r["reference"],
                "company":        r["company"],
                "source_type":    r["source_type"],
                "doc_id":         r["doc_id"],
                "contexts_count": len(r["retrieved_contexts"]),
            })
    print(f"[저장] 결과 CSV: {csv_path}")

    # 요약 JSON
    summary = {
        "timestamp":   datetime.now().isoformat(),
        "total_qa":    len(rows),
        "ragas_scores": scores,
    }
    json_path = out_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[저장] 요약 JSON: {json_path}")


def print_summary(scores: dict, total: int) -> None:
    print("\n========== RAGAS 평가 결과 ==========")
    print(f"  평가 QA 수 : {total:,}개")
    if scores:
        for k, v in scores.items():
            bar = "█" * int(v * 20)
            print(f"  {k:<25}: {v:.4f}  {bar}")
        # GAIA 목표 기준
        targets = {"faithfulness": 0.7, "answer_relevancy": 0.6,
                   "context_precision": 0.5, "context_recall": 0.5}
        print("\n  [GAIA 목표 기준]")
        for k, t in targets.items():
            if k in scores:
                ok = "PASS" if scores[k] >= t else "FAIL"
                print(f"    {k:<25}: {scores[k]:.4f} (목표 {t}) → {ok}")
    else:
        print("  RAGAS 점수를 계산하지 못했습니다.")
    print("=====================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qdrant RAG RAGAS 평가")
    parser.add_argument("--limit",   type=int, default=None, help="평가할 QA 수 제한")
    parser.add_argument("--company", type=str, default=None, help="특정 회사명 필터 (인제스트와 맞춰서 사용)")
    parser.add_argument("--out",     type=str, default="qdrant_eval", help="결과 파일 기본 이름")
    args = parser.parse_args()

    qa_pairs = load_qa_pairs(limit=args.limit, company_filter=args.company)
    rows     = run_eval(qa_pairs, company_filter=args.company)
    scores   = compute_ragas(rows)

    out_path = OUTPUT_DIR / args.out
    save_results(rows, scores, out_path)
    print_summary(scores, len(rows))

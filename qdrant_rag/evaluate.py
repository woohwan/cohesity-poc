"""
RAGAS로 Qdrant RAG 파이프라인 품질 평가

사전 조건:
  - gaia_ragas/output/qa_pairs.json 가 생성되어 있어야 함
  - Qdrant 컬렉션에 문서가 인제스트되어 있어야 함
  - LLM API 키 설정 (LLM_PROVIDER=claude → ANTHROPIC_API_KEY, chatgpt → OPENAI_API_KEY)

사용법:
  python evaluate.py
  python evaluate.py --limit 20        # QA 20개만
  python evaluate.py --out my_eval.csv # 결과 파일명 지정
"""
import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR, TOP_K
from qa_chain import ask

def _resolve_qa_path(company_filter: str = None) -> Path:
    """
    회사 필터가 있으면 qa_pairs_{company}.json 우선 탐색.
    없으면 qa_pairs.json 사용.
    """
    output_dir = OUTPUT_DIR
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
        print("  먼저 QA 생성을 실행하세요:")
        print("    ./run.sh qa-gen [--company 회사명]")
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


def _install_ragas_compat_shims() -> None:
    """RAGAS 일부 버전이 참조하는 구 LangChain VertexAI 경로를 호환 처리한다."""
    import sys
    import types

    module_name = "langchain_community.chat_models.vertexai"
    if module_name in sys.modules:
        return

    module = types.ModuleType(module_name)

    class ChatVertexAI:  # pragma: no cover - 실제 평가에서는 사용하지 않는 호환 shim
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "ChatVertexAI is not installed. "
                "Set LLM_PROVIDER=claude or chatgpt for this evaluator."
            )

    module.ChatVertexAI = ChatVertexAI
    sys.modules[module_name] = module


def _build_ragas_embeddings():
    """embed 서버(우선) 또는 로컬 HuggingFace 모델을 LangchainEmbeddingsWrapper로 감싸 반환."""
    from config import EMBED_MODEL, EMBED_SERVER_URL
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_core.embeddings import Embeddings

    if EMBED_SERVER_URL:
        from retriever import _server_post

        class _EmbedServerEmbeddings(Embeddings):
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                resp = _server_post("/embed/dense", {"texts": texts, "normalize": True})
                return resp["vectors"]

            def embed_query(self, text: str) -> list[float]:
                return self.embed_documents([text])[0]

        return LangchainEmbeddingsWrapper(_EmbedServerEmbeddings())

    from langchain_huggingface import HuggingFaceEmbeddings
    return LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=EMBED_MODEL))


def compute_ragas(rows: list[dict]) -> dict:
    """RAGAS 메트릭 계산 (4가지: faithfulness, answer_relevancy, context_precision, context_recall)."""
    if not rows:
        print("[RAGAS] 평가할 데이터가 없습니다 (RAG 응답 실패).")
        return {}
    try:
        _install_ragas_compat_shims()
        from ragas import evaluate
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
        from ragas.llms import LangchainLLMWrapper
        try:
            from ragas.metrics import (
                Faithfulness, AnswerRelevancy,
                ContextPrecision, ContextRecall,
            )
            metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]
        except ImportError:
            from ragas.metrics import (  # noqa: E402  (older ragas)
                faithfulness, answer_relevancy,
                context_precision, context_recall,
            )
            metrics = [faithfulness, answer_relevancy, context_precision, context_recall]

        from llm_client import build_langchain_llm

        ragas_llm = LangchainLLMWrapper(build_langchain_llm())

        try:
            ragas_embeddings = _build_ragas_embeddings()
            print("[RAGAS] embeddings: embed 서버 또는 로컬 HuggingFace 사용")
        except Exception as e:
            print(f"[WARN] RAGAS embeddings 설정 실패: {e}")
            ragas_embeddings = None

        samples = []
        for r in rows:
            samples.append(SingleTurnSample(
                user_input=r["user_input"],
                reference=r["reference"],
                response=r["response"],
                retrieved_contexts=r["retrieved_contexts"],
            ))

        dataset = EvaluationDataset(samples=samples)

        for m in metrics:
            if hasattr(m, "llm"):
                m.llm = ragas_llm
            if ragas_embeddings and hasattr(m, "embeddings"):
                m.embeddings = ragas_embeddings

        eval_kwargs = {"dataset": dataset, "metrics": metrics, "llm": ragas_llm}
        if ragas_embeddings:
            eval_kwargs["embeddings"] = ragas_embeddings

        result = evaluate(**eval_kwargs)
        return result.to_pandas().mean(numeric_only=True).to_dict()

    except Exception as e:
        print(f"[RAGAS] 메트릭 계산 실패: {e}")
        return {}


def _result_records(rows: list[dict]) -> list[dict]:
    records = []
    for i, r in enumerate(rows, 1):
        records.append({
            "no": i,
            "question": r["user_input"],
            "rag_response": r["response"],
            "reference_answer": r["reference"],
            "company": r["company"],
            "source_type": r["source_type"],
            "doc_id": r["doc_id"],
            "contexts_count": len(r["retrieved_contexts"]),
            "retrieved_contexts": "\n---\n".join(r["retrieved_contexts"]),
        })
    return records


def save_results(rows: list[dict], scores: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = _result_records(rows)

    # CSV 저장
    csv_path = out_path.with_suffix(".csv")
    fieldnames = [
        "no", "question", "rag_response", "reference_answer",
        "company", "source_type", "doc_id", "contexts_count",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record[k] for k in fieldnames})
    print(f"[저장] 결과 CSV: {csv_path}")

    # Excel 저장
    xlsx_path = out_path.with_suffix(".xlsx")
    try:
        import pandas as pd

        scores_records = [
            {"metric": k, "score": v}
            for k, v in scores.items()
        ]
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            pd.DataFrame(records).to_excel(writer, index=False, sheet_name="results")
            pd.DataFrame(scores_records).to_excel(writer, index=False, sheet_name="ragas_scores")
        print(f"[저장] 결과 Excel: {xlsx_path}")
    except Exception as e:
        print(f"[WARN] Excel 저장 실패: {e}")

    # 요약 JSON
    summary = {
        "timestamp":   datetime.now().isoformat(),
        "total_qa":    len(rows),
        "ragas_scores": scores,
        "files": {
            "csv": str(csv_path),
            "xlsx": str(xlsx_path),
        },
    }
    json_path = out_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[저장] 요약 JSON: {json_path}")


def _shorten(text: str, limit: int = 260) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def print_eval_rows(rows: list[dict]) -> None:
    print("\n========== RAG 응답 상세 ==========")
    if not rows:
        print("  출력할 평가 결과가 없습니다.")
    for i, r in enumerate(rows, 1):
        print(f"\n[{i}] 질문")
        print(f"  {_shorten(r['user_input'], 220)}")
        print("  RAG 답변")
        print(f"  {_shorten(r['response'], 420)}")
        print("  기준 답변")
        print(f"  {_shorten(r['reference'], 420)}")
        print(f"  참조 문서 수: {len(r['retrieved_contexts'])} | 회사: {r['company']} | 유형: {r['source_type']}")
    print("===================================\n")


def print_summary(scores: dict, total: int) -> None:
    print("\n========== RAGAS 평가 결과 ==========")
    print(f"  평가 QA 수 : {total:,}개")
    if scores:
        printable_scores = {k: v for k, v in scores.items() if isinstance(v, (int, float)) and not math.isnan(v)}
        if not printable_scores:
            print("  RAGAS 점수가 모두 NaN입니다. 위쪽 RAGAS 예외 로그를 확인하세요.")
            print("=====================================\n")
            return
        for k, v in printable_scores.items():
            bar = "█" * int(v * 20)
            print(f"  {k:<25}: {v:.4f}  {bar}")
        # GAIA 목표 기준
        targets = {"faithfulness": 0.7, "answer_relevancy": 0.6,
                   "context_precision": 0.5, "context_recall": 0.5}
        print("\n  [GAIA 목표 기준]")
        for k, t in targets.items():
            if k in printable_scores:
                ok = "PASS" if printable_scores[k] >= t else "FAIL"
                print(f"    {k:<25}: {printable_scores[k]:.4f} (목표 {t}) → {ok}")
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
    print_eval_rows(rows)
    save_results(rows, scores, out_path)
    print_summary(scores, len(rows))

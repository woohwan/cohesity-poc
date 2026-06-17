"""
Cohesity GAIA API를 호출해 테스트셋으로 평가한다.

Cohesity GAIA REST API 엔드포인트:
  POST /gaia/ask
  {
    "datasetNames": ["dataset-name"],
    "queryString": "질문"
  }

사용 전에 환경 변수를 설정하세요:
  COHESITY_CLUSTER_URL  : https://<helios-fqdn>
  COHESITY_API_KEY      : Helios API Key (Settings > Access Management > API Keys)
  COHESITY_DATASET_NAME : GAIA 데이터셋 이름 (GAIA_VIEW 권한 필요)

평가 메트릭:
  - Exact Match (EM)
  - 부분 포함 여부 (Containment)
  - RAGAS 메트릭 (faithfulness, answer_relevancy, context_precision 등)
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests
import pandas as pd

from config import OUTPUT_DIR


CLUSTER_URL   = os.environ.get("COHESITY_CLUSTER_URL", "")
API_KEY       = os.environ.get("COHESITY_API_KEY", "")
DATASET_NAME  = os.environ.get("COHESITY_DATASET_NAME", "")


def query_gaia(question: str, top_k: int = 5) -> dict:
    """GAIA API에 질문을 보내고 응답을 반환한다."""
    if not CLUSTER_URL or not API_KEY:
        raise ValueError("COHESITY_CLUSTER_URL, COHESITY_API_KEY 환경 변수를 설정하세요.")

    headers = {
        "apiKey": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "queryString": question,
    }
    if DATASET_NAME:
        payload["datasetNames"] = [DATASET_NAME]

    url = f"{CLUSTER_URL}/gaia/ask"
    resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=60)
    resp.raise_for_status()
    return resp.json()


def extract_gaia_answer(response: dict) -> tuple[str, list[str]]:
    """
    GAIA 응답에서 답변 텍스트와 검색된 컨텍스트를 추출한다.
    GAIA 버전에 따라 응답 구조가 다를 수 있으므로 여러 키를 시도한다.
    """
    answer = (
        response.get("answer")
        or response.get("response")
        or response.get("text")
        or ""
    )

    contexts = []
    for key in ("contexts", "chunks", "documents", "results"):
        items = response.get(key, [])
        if items:
            for item in items:
                text = item.get("text") or item.get("content") or item.get("chunk") or ""
                if text:
                    contexts.append(text)
            break

    return answer, contexts


def evaluate_exact_match(predicted: str, reference: str) -> bool:
    return predicted.strip() == reference.strip()


def evaluate_containment(predicted: str, reference: str) -> float:
    """참조 답변의 핵심 키워드가 예측에 얼마나 포함되어 있는지 측정."""
    ref_tokens = set(reference.replace(" ", ""))
    pred_tokens = set(predicted.replace(" ", ""))
    if not ref_tokens:
        return 0.0
    overlap = ref_tokens & pred_tokens
    return len(overlap) / len(ref_tokens)


def run_gaia_evaluation(
    testset_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    max_samples: Optional[int] = None,
    delay_seconds: float = 1.0,
) -> pd.DataFrame:
    """
    GAIA 테스트셋을 이용해 평가를 실행하고 결과를 저장한다.
    """
    if testset_path is None:
        testset_path = OUTPUT_DIR / "ragas_testset.json"
    if output_path is None:
        output_path = OUTPUT_DIR / "gaia_eval_results.csv"

    with open(testset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    samples = data["samples"] if "samples" in data else data

    if max_samples:
        samples = samples[:max_samples]

    print(f"{len(samples)}개 샘플로 GAIA 평가 시작...")

    rows = []
    for i, sample in enumerate(samples):
        question = sample["user_input"]
        reference = sample["reference"]

        print(f"[{i+1}/{len(samples)}] {question[:50]}...")

        try:
            response = query_gaia(question)
            predicted, contexts = extract_gaia_answer(response)
            em = evaluate_exact_match(predicted, reference)
            containment = evaluate_containment(predicted, reference)
            status = "ok"
        except Exception as e:
            predicted = ""
            contexts = []
            em = False
            containment = 0.0
            status = f"error: {e}"

        rows.append({
            "no": i + 1,
            "question": question,
            "reference": reference,
            "gaia_response": predicted,
            "exact_match": em,
            "containment": round(containment, 3),
            "retrieved_context_count": len(contexts),
            "company": sample.get("company", ""),
            "report_type": sample.get("report_type", ""),
            "question_type": sample.get("question_type", ""),
            "status": status,
        })

        time.sleep(delay_seconds)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\n========== GAIA 평가 결과 ==========")
    print(f"총 샘플: {len(df)}")
    print(f"Exact Match: {df['exact_match'].mean():.1%}")
    print(f"Avg Containment: {df['containment'].mean():.3f}")
    print(f"오류 발생: {(df['status'] != 'ok').sum()}건")
    print(f"[저장] 결과 → {output_path}")

    return df


def run_ragas_evaluation(
    testset_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> None:
    """
    GAIA 평가 결과에 RAGAS 메트릭을 적용한다.
    gaia_evaluator.run_gaia_evaluation() 실행 후 호출한다.
    """
    try:
        from ragas import evaluate
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness, AnswerRelevancy,
            ContextPrecision, ContextRecall,
        )
        from llm_client import build_langchain_llm
    except ImportError:
        print("[SKIP] ragas 패키지가 없어 RAGAS 평가를 건너뜁니다.")
        return

    try:
        ragas_llm = LangchainLLMWrapper(build_langchain_llm())
    except Exception as e:
        print(f"[WARN] LLM 설정 실패: {e}. RAGAS 기본 LLM을 사용합니다.")
        ragas_llm = None

    # OpenAI embedding fallback 방지 — 소형 HuggingFace 모델 사용
    ragas_embeddings = None
    try:
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from langchain_huggingface import HuggingFaceEmbeddings
        ragas_embeddings = LangchainEmbeddingsWrapper(
            HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        )
    except Exception as e:
        print(f"[WARN] embeddings 설정 실패 (OpenAI fallback 가능): {e}")

    if testset_path is None:
        testset_path = OUTPUT_DIR / "ragas_testset.json"
    if output_path is None:
        output_path = OUTPUT_DIR / "ragas_eval_results.csv"

    eval_csv = OUTPUT_DIR / "gaia_eval_results.csv"
    if not eval_csv.exists():
        print("[ERROR] gaia_eval_results.csv가 없습니다. run_gaia_evaluation()을 먼저 실행하세요.")
        return

    gaia_df = pd.read_csv(eval_csv)

    # RAGAS 데이터셋 구성
    with open(testset_path, "r", encoding="utf-8") as f:
        testset = json.load(f)
    samples = testset["samples"] if "samples" in testset else testset

    ragas_samples = []
    for row, sample in zip(gaia_df.itertuples(), samples):
        ragas_samples.append(SingleTurnSample(
            user_input=sample["user_input"],
            response=row.gaia_response if row.gaia_response else "",
            retrieved_contexts=sample.get("retrieved_contexts", []),
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

    print("RAGAS 평가 실행 중...")
    result = evaluate(**eval_kwargs)

    result_df = result.to_pandas()
    result_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\n========== RAGAS 평가 결과 ==========")
    for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        if metric in result_df.columns:
            print(f"{metric}: {result_df[metric].mean():.3f}")
    print(f"[저장] RAGAS 결과 → {output_path}")


if __name__ == "__main__":
    # GAIA 평가 (API 연결 필요)
    run_gaia_evaluation()
    # RAGAS 평가 (위 실행 후)
    run_ragas_evaluation()

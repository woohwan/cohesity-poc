"""
Cohesity GAIA API를 호출해 테스트셋으로 평가한다.

Cohesity GAIA REST API 엔드포인트 (예시):
  POST /api/v1/gaia/query
  {
    "query": "질문",
    "collection_ids": ["..."],
    "max_results": 5
  }

사용 전에 환경 변수를 설정하세요:
  COHESITY_CLUSTER_URL  : https://<cluster-ip>
  COHESITY_API_TOKEN    : Bearer 토큰
  COHESITY_COLLECTION_ID: GAIA 컬렉션 ID

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


CLUSTER_URL = os.environ.get("COHESITY_CLUSTER_URL", "")
API_TOKEN = os.environ.get("COHESITY_API_TOKEN", "")
COLLECTION_ID = os.environ.get("COHESITY_COLLECTION_ID", "")


def query_gaia(question: str, top_k: int = 5) -> dict:
    """GAIA API에 질문을 보내고 응답을 반환한다."""
    if not CLUSTER_URL or not API_TOKEN:
        raise ValueError("COHESITY_CLUSTER_URL, COHESITY_API_TOKEN 환경 변수를 설정하세요.")

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": question,
        "max_results": top_k,
    }
    if COLLECTION_ID:
        payload["collection_ids"] = [COLLECTION_ID]

    # GAIA 엔드포인트는 클러스터 버전에 따라 다를 수 있음
    url = f"{CLUSTER_URL}/api/v1/gaia/query"
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
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
        )
        from datasets import Dataset
    except ImportError:
        print("[SKIP] ragas 또는 datasets 패키지가 없어 RAGAS 평가를 건너뜁니다.")
        return

    try:
        import anthropic as _anthropic
        from langchain_anthropic import ChatAnthropic
        from ragas.llms import LangchainLLMWrapper

        llm = LangchainLLMWrapper(ChatAnthropic(model="claude-sonnet-4-6"))
    except Exception as e:
        print(f"[WARN] LLM 설정 실패: {e}. RAGAS 기본 LLM을 사용합니다.")
        llm = None

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

    ragas_data = []
    for row, sample in zip(gaia_df.itertuples(), samples):
        ragas_data.append({
            "user_input": sample["user_input"],
            "response": row.gaia_response if row.gaia_response else "",
            "retrieved_contexts": sample.get("retrieved_contexts", []),
            "reference": sample["reference"],
        })

    dataset = Dataset.from_list(ragas_data)

    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    kwargs = {"llm": llm} if llm else {}

    print("RAGAS 평가 실행 중...")
    result = evaluate(dataset, metrics=metrics, **kwargs)

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

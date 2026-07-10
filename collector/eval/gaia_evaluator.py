"""
Cohesity GAIA API를 호출해 타입별 RAGAS 테스트셋으로 실제 GAIA 성능을 평가한다.

Cohesity GAIA REST API 엔드포인트:
  POST /gaia/ask
  {
    "datasetNames": ["dataset-name"],
    "queryString": "질문"
  }

사용 전에 환경 변수를 설정하세요:
  COHESITY_CLUSTER_URL  : https://<helios-fqdn>
  COHESITY_API_KEY      : Helios API Key (Settings > Access Management > API Keys)
  COHESITY_DATASET_NAME : GAIA 데이터셋 이름 (이 저장소의 문서들이 미리 GAIA에
                          색인(ingest)되어 있어야 함 — 이 스크립트는 색인은 하지 않고
                          질의만 한다)

평가 메트릭:
  - Exact Match (EM) / Containment : 문자열 비교, LLM 불필요
  - RAGAS 4개 메트릭 (faithfulness, answer_relevancy,
    context_precision, context_recall) : pip 패키지 `ragas` 사용.
    `ragas>=0.2.0`이 langchain-community에서 이미 제거된
    `chat_models.vertexai` 서브모듈을 무조건 import하는 문제가 있어
    `_ragas_compat` shim으로 우회한다 (아래 import 순서 유지 필수).
"""
import _ragas_compat  # noqa: F401 — ragas import 전에 반드시 먼저 (vertexai shim 등록)

import json
import os
import time
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from tqdm import tqdm

from config import OUTPUT_DIR, TYPE_GROUPS


CLUSTER_URL  = os.environ.get("COHESITY_CLUSTER_URL", "")
API_KEY      = os.environ.get("COHESITY_API_KEY", "")
DATASET_NAME = os.environ.get("COHESITY_DATASET_NAME", "")


# ── GAIA API 호출 ────────────────────────────────────────────────────────────

def query_gaia(question: str) -> dict:
    """GAIA API에 질문을 보내고 응답을 반환한다."""
    if not CLUSTER_URL or not API_KEY:
        raise ValueError("COHESITY_CLUSTER_URL, COHESITY_API_KEY 환경 변수를 설정하세요.")

    headers = {"apiKey": API_KEY, "Content-Type": "application/json"}
    payload = {"queryString": question}
    if DATASET_NAME:
        payload["datasetNames"] = [DATASET_NAME]

    url = f"{CLUSTER_URL}/gaia/ask"
    resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=60)
    resp.raise_for_status()
    return resp.json()


def extract_gaia_answer(response: dict) -> tuple[str, list[str]]:
    """GAIA 응답에서 답변 텍스트와 검색된 컨텍스트를 추출한다."""
    answer = response.get("answer") or response.get("response") or response.get("text") or ""

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
    """참조 답변의 문자 집합이 예측 답변에 얼마나 포함되어 있는지 (간이 지표)."""
    ref_tokens = set(reference.replace(" ", ""))
    pred_tokens = set(predicted.replace(" ", ""))
    if not ref_tokens:
        return 0.0
    return len(ref_tokens & pred_tokens) / len(ref_tokens)


def run_gaia_evaluation(
    group_key: str,
    max_samples: Optional[int] = None,
    delay_seconds: float = 1.0,
) -> pd.DataFrame:
    """group_key(타입명 또는 소스명)의 RAGAS 테스트셋으로 GAIA API를 호출해 평가한다."""
    testset_path = OUTPUT_DIR / f"ragas_testset_{group_key}.json"
    output_path = OUTPUT_DIR / f"gaia_eval_results_{group_key}.csv"

    with open(testset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    samples = data["samples"] if "samples" in data else data
    if max_samples:
        samples = samples[:max_samples]

    print(f"[{group_key}] {len(samples)}개 샘플로 GAIA 평가 시작...")

    rows = []
    for i, sample in enumerate(tqdm(samples, desc=f"GAIA 질의[{group_key}]", unit="q")):
        question = sample["user_input"]
        reference = sample["reference"]

        try:
            response = query_gaia(question)
            predicted, contexts = extract_gaia_answer(response)
            em = evaluate_exact_match(predicted, reference)
            containment = evaluate_containment(predicted, reference)
            status = "ok"
        except Exception as e:
            predicted, contexts, em, containment = "", [], False, 0.0
            status = f"error: {e}"

        rows.append({
            "no": i + 1,
            "question": question,
            "reference": reference,
            "gaia_response": predicted,
            "retrieved_contexts": json.dumps(contexts, ensure_ascii=False),
            "exact_match": em,
            "containment": round(containment, 3),
            "retrieved_context_count": len(contexts),
            "type_group": sample.get("type_group", ""),
            "ext": sample.get("ext", ""),
            "source": sample.get("source", ""),
            "question_type": sample.get("question_type", ""),
            "status": status,
        })
        time.sleep(delay_seconds)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\n===== GAIA 평가 결과 [{group_key}] =====")
    print(f"총 샘플: {len(df)}")
    print(f"Exact Match: {df['exact_match'].mean():.1%}")
    print(f"Avg Containment: {df['containment'].mean():.3f}")
    print(f"오류 발생: {(df['status'] != 'ok').sum()}건")
    print(f"[저장] 결과 → {output_path}")
    return df


# ── RAGAS 평가 (pip 패키지 ragas 사용) ────────────────────────────────────────

def _build_ragas_llm():
    """LLM_PROVIDER(claude|chatgpt)에 맞는 LangchainLLMWrapper를 만든다 (qa_generator.py와 동일한 provider 선택 규칙)."""
    from ragas.llms import LangchainLLMWrapper
    from llm_client import normalized_provider
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, OPENAI_API_KEY, LLM_MODEL

    if normalized_provider() == "chatgpt":
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
        from langchain_openai import ChatOpenAI
        return LangchainLLMWrapper(ChatOpenAI(model=LLM_MODEL, api_key=OPENAI_API_KEY))

    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
    from langchain_anthropic import ChatAnthropic
    return LangchainLLMWrapper(ChatAnthropic(model=CLAUDE_MODEL, api_key=ANTHROPIC_API_KEY))


def _build_ragas_embeddings():
    """OpenAI embedding fallback을 피하기 위해 다국어 HuggingFace 임베딩을 사용한다.
    모델이 작아 CPU로도 충분하고, GPU를 쓰면 이 머신처럼 다른 프로세스가 이미
    GPU 메모리를 점유 중일 때 OOM이 날 수 있어 CPU로 고정한다."""
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_huggingface import HuggingFaceEmbeddings

    return LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            model_kwargs={"device": "cpu"},
        )
    )


def run_ragas_evaluation(group_key: str) -> Optional[pd.DataFrame]:
    """gaia_eval_results_{group_key}.csv를 읽어 pip 패키지 ragas로 4개 메트릭을 채점한다."""
    from ragas import evaluate
    from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
    from ragas.metrics import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall

    eval_csv = OUTPUT_DIR / f"gaia_eval_results_{group_key}.csv"
    output_path = OUTPUT_DIR / f"ragas_eval_results_{group_key}.csv"

    if not eval_csv.exists():
        print(f"[ERROR] {eval_csv} 가 없습니다. run_gaia_evaluation()을 먼저 실행하세요.")
        return None

    df = pd.read_csv(eval_csv)

    samples = []
    for row in df.to_dict("records"):
        contexts = row.get("retrieved_contexts", "[]")
        if isinstance(contexts, str):
            try:
                contexts = json.loads(contexts)
            except (json.JSONDecodeError, TypeError):
                contexts = [contexts] if contexts else []
        samples.append(SingleTurnSample(
            user_input=row["question"],
            response=row.get("gaia_response") or "",
            retrieved_contexts=contexts or [""],
            reference=row["reference"],
        ))

    dataset = EvaluationDataset(samples)
    llm = _build_ragas_llm()
    embeddings = _build_ragas_embeddings()
    metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]

    print(f"RAGAS 평가 실행 중 [{group_key}] ({len(samples)}개 샘플)...")
    result = evaluate(dataset=dataset, metrics=metrics, llm=llm, embeddings=embeddings)

    result_df = result.to_pandas()
    # 원본 메타데이터(type_group/ext/source 등) 다시 붙이기
    meta_cols = [c for c in ("type_group", "ext", "source", "question_type") if c in df.columns]
    for c in meta_cols:
        result_df[c] = df[c].values
    result_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\n===== RAGAS 평가 결과 [{group_key}] =====")
    for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        if metric in result_df.columns:
            print(f"{metric}: {result_df[metric].mean():.3f}")
    print(f"[저장] RAGAS 결과 → {output_path}")
    return result_df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--type", choices=list(TYPE_GROUPS.keys()))
    group.add_argument("--source", help="이전 단계를 --group-by source 로 실행한 경우의 소스명")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--skip-ragas", action="store_true", help="GAIA 질의만 하고 RAGAS 채점은 생략")
    args = parser.parse_args()

    key = args.type or args.source
    run_gaia_evaluation(key, max_samples=args.max_samples)
    if not args.skip_ragas:
        run_ragas_evaluation(key)

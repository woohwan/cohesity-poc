"""
QA 쌍을 RAGAS 평가용 테스트셋으로 변환한다.

RAGAS SingleTurnSample 구조:
  - user_input   : 질문
  - reference    : 정답 (ground truth)
  - retrieved_contexts: 검색된 컨텍스트 (여기서는 원문 텍스트로 초기화)
  - response     : RAG 시스템 응답 (평가 시 채워짐)

출력:
  - ragas_testset.json  : RAGAS 평가용 JSON
  - gaia_eval.csv       : Cohesity GAIA 평가용 CSV
  - ragas_testset_hf/   : HuggingFace datasets 형식 (선택)
"""
import json
import csv
import random
from pathlib import Path
from typing import Optional

import pandas as pd

from config import OUTPUT_DIR, RAGAS_MIN_SAMPLES, RANDOM_SEED


def load_qa_pairs(path: Optional[Path] = None) -> list[dict]:
    if path is None:
        path = OUTPUT_DIR / "qa_pairs.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ragas_samples(qa_pairs: list[dict]) -> list[dict]:
    """
    QA 쌍을 RAGAS SingleTurnSample 호환 딕셔너리로 변환.
    source_text를 retrieved_contexts 초기값으로 사용한다.
    실제 평가 시에는 RAG 시스템이 retrieved_contexts와 response를 채운다.
    """
    samples = []
    for qa in qa_pairs:
        source_text = qa.get("source_text", "")
        samples.append({
            "user_input": qa["question"],
            "reference": qa["answer"],
            "retrieved_contexts": [source_text] if source_text else [],
            "response": "",  # RAG 시스템이 채울 필드
            # 메타데이터
            "doc_id": qa.get("doc_id", ""),
            "company": qa.get("company", ""),
            "company_code": qa.get("company_code", ""),
            "report_type": qa.get("report_type", ""),
            "filing_date": qa.get("filing_date", ""),
            "dataset": qa.get("dataset", ""),
            "source_type": qa.get("source_type", ""),
            "question_type": qa.get("question_type", ""),
        })
    return samples


def save_ragas_json(samples: list[dict], path: Optional[Path] = None) -> Path:
    """RAGAS 평가용 JSON 저장."""
    if path is None:
        path = OUTPUT_DIR / "ragas_testset.json"

    ragas_format = {
        "version": "1.0",
        "description": "DART KOSPI200 공시 문서 기반 RAGAS 테스트셋",
        "total_samples": len(samples),
        "samples": samples,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ragas_format, f, ensure_ascii=False, indent=2)
    print(f"[저장] RAGAS 테스트셋 ({len(samples)}개) → {path}")
    return path


def save_gaia_eval_csv(samples: list[dict], path: Optional[Path] = None) -> Path:
    """Cohesity GAIA 평가용 CSV 저장 (question, expected_answer, doc_id, company 등)."""
    if path is None:
        path = OUTPUT_DIR / "gaia_eval.csv"

    fieldnames = [
        "no", "question", "expected_answer", "question_type",
        "doc_id", "company", "company_code", "report_type",
        "filing_date", "dataset", "source_type",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀 한글 호환
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, s in enumerate(samples, 1):
            writer.writerow({
                "no": i,
                "question": s["user_input"],
                "expected_answer": s["reference"],
                "question_type": s.get("question_type", ""),
                "doc_id": s.get("doc_id", ""),
                "company": s.get("company", ""),
                "company_code": s.get("company_code", ""),
                "report_type": s.get("report_type", ""),
                "filing_date": s.get("filing_date", ""),
                "dataset": s.get("dataset", ""),
                "source_type": s.get("source_type", ""),
            })
    print(f"[저장] GAIA 평가 CSV ({len(samples)}개) → {path}")
    return path


def save_ragas_hf_dataset(samples: list[dict], path: Optional[Path] = None) -> Path:
    """HuggingFace datasets 형식으로 저장 (ragas.evaluate()에 직접 사용 가능)."""
    try:
        from datasets import Dataset
    except ImportError:
        print("[SKIP] datasets 패키지가 없어 HF 형식 저장을 건너뜁니다.")
        return None

    if path is None:
        path = OUTPUT_DIR / "ragas_testset_hf"

    df = pd.DataFrame([{
        "user_input": s["user_input"],
        "reference": s["reference"],
        "retrieved_contexts": s["retrieved_contexts"],
        "response": s["response"],
    } for s in samples])

    dataset = Dataset.from_pandas(df)
    dataset.save_to_disk(str(path))
    print(f"[저장] HuggingFace Dataset ({len(samples)}개) → {path}")
    return path


def print_statistics(samples: list[dict]) -> None:
    """테스트셋 통계 출력."""
    df = pd.DataFrame(samples)
    print("\n========== RAGAS 테스트셋 통계 ==========")
    print(f"총 샘플 수: {len(df)}")
    print(f"\n데이터셋별 분포:")
    print(df["dataset"].value_counts().to_string())
    print(f"\n보고서 유형별 분포:")
    print(df["report_type"].value_counts().to_string())
    print(f"\n질문 유형별 분포:")
    print(df["question_type"].value_counts().to_string())
    print(f"\n회사 수: {df['company'].nunique()}")
    avg_q_len = df["user_input"].str.len().mean()
    avg_a_len = df["reference"].str.len().mean()
    print(f"평균 질문 길이: {avg_q_len:.0f}자")
    print(f"평균 답변 길이: {avg_a_len:.0f}자")
    print("==========================================\n")


def create_testset(
    qa_path: Optional[Path] = None,
    min_samples: int = RAGAS_MIN_SAMPLES,
    seed: int = RANDOM_SEED,
) -> list[dict]:
    """
    QA 쌍 파일에서 RAGAS 테스트셋을 생성한다.
    min_samples 미만이면 경고를 출력한다.
    """
    qa_pairs = load_qa_pairs(qa_path)
    print(f"[로드] QA 쌍 {len(qa_pairs)}개 로드.")

    if len(qa_pairs) < min_samples:
        print(f"[WARN] QA 쌍 수({len(qa_pairs)})가 목표({min_samples})보다 적습니다.")

    samples = build_ragas_samples(qa_pairs)

    # 최소 수량 이상이면 랜덤 셔플 후 목표 수량으로 슬라이스
    rng = random.Random(seed)
    rng.shuffle(samples)

    save_ragas_json(samples)
    save_gaia_eval_csv(samples)
    save_ragas_hf_dataset(samples)
    print_statistics(samples)

    return samples


if __name__ == "__main__":
    samples = create_testset()

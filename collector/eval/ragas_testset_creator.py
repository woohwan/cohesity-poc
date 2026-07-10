"""
QA 쌍을 RAGAS 평가용 테스트셋(SingleTurnSample 호환)으로 변환한다.
그룹(타입 또는 소스=토픽) 기준으로 별도 파일을 생성한다.
"""
import json
import random
from pathlib import Path
from typing import Optional

import pandas as pd

from config import OUTPUT_DIR, RANDOM_SEED


def load_qa_pairs(group_key: str) -> list[dict]:
    path = OUTPUT_DIR / f"qa_pairs_{group_key}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ragas_samples(qa_pairs: list[dict]) -> list[dict]:
    samples = []
    for qa in qa_pairs:
        source_text = qa.get("source_text", "")
        samples.append({
            "user_input": qa["question"],
            "reference": qa["answer"],
            "retrieved_contexts": [source_text] if source_text else [],
            "response": "",
            "doc_id": qa.get("doc_id", ""),
            "type_group": qa.get("type_group", ""),
            "ext": qa.get("ext", ""),
            "source": qa.get("source", ""),
            "file_name": qa.get("file_name", ""),
            "question_type": qa.get("question_type", ""),
        })
    return samples


def save_ragas_json(samples: list[dict], group_key: str) -> Path:
    path = OUTPUT_DIR / f"ragas_testset_{group_key}.json"
    ragas_format = {
        "version": "1.0",
        "description": f"Cohesity GAIA KR 수집 데이터 — {group_key} 그룹 RAGAS 테스트셋",
        "group_key": group_key,
        "total_samples": len(samples),
        "samples": samples,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ragas_format, f, ensure_ascii=False, indent=2)
    print(f"[저장] RAGAS 테스트셋 ({len(samples)}개) → {path}")
    return path


def save_gaia_eval_csv(samples: list[dict], group_key: str) -> Path:
    path = OUTPUT_DIR / f"gaia_eval_{group_key}.csv"
    fieldnames = [
        "no", "question", "expected_answer", "question_type",
        "doc_id", "type_group", "ext", "source", "file_name",
    ]
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, s in enumerate(samples, 1):
            writer.writerow({
                "no": i,
                "question": s["user_input"],
                "expected_answer": s["reference"],
                "question_type": s.get("question_type", ""),
                "doc_id": s.get("doc_id", ""),
                "type_group": s.get("type_group", ""),
                "ext": s.get("ext", ""),
                "source": s.get("source", ""),
                "file_name": s.get("file_name", ""),
            })
    print(f"[저장] GAIA 평가 CSV ({len(samples)}개) → {path}")
    return path


def save_ragas_hf_dataset(samples: list[dict], group_key: str) -> Optional[Path]:
    try:
        from datasets import Dataset
    except ImportError:
        print("[SKIP] datasets 패키지가 없어 HF 형식 저장을 건너뜁니다.")
        return None
    path = OUTPUT_DIR / f"ragas_testset_hf_{group_key}"
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


def print_statistics(samples: list[dict], group_key: str) -> None:
    df = pd.DataFrame(samples)
    print(f"\n========== RAGAS 테스트셋 통계 [{group_key}] ==========")
    print(f"총 샘플 수: {len(df)}")
    print("\n확장자별 분포:")
    print(df["ext"].value_counts().to_string())
    print("\n소스별 분포 (상위 10):")
    print(df["source"].value_counts().head(10).to_string())
    print("\n질문 유형별 분포:")
    print(df["question_type"].value_counts().to_string())
    avg_q_len = df["user_input"].str.len().mean()
    avg_a_len = df["reference"].str.len().mean()
    print(f"평균 질문 길이: {avg_q_len:.0f}자")
    print(f"평균 답변 길이: {avg_a_len:.0f}자")
    print("=========================================================\n")


def create_testset(group_key: str, seed: int = RANDOM_SEED) -> list[dict]:
    """group_key: 타입명(pdf 등) 또는 소스명(bok_publications 등)."""
    qa_pairs = load_qa_pairs(group_key)
    print(f"[로드] QA 쌍 {len(qa_pairs)}개 로드 [{group_key}].")

    samples = build_ragas_samples(qa_pairs)

    rng = random.Random(seed)
    rng.shuffle(samples)

    save_ragas_json(samples, group_key)
    save_gaia_eval_csv(samples, group_key)
    save_ragas_hf_dataset(samples, group_key)
    print_statistics(samples, group_key)

    return samples


if __name__ == "__main__":
    import argparse
    from config import TYPE_GROUPS

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--type", choices=list(TYPE_GROUPS.keys()))
    group.add_argument("--source", help="sample/qa 단계를 --group-by source 로 실행한 경우의 소스명")
    args = parser.parse_args()

    create_testset(args.type or args.source)

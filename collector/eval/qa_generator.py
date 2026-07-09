"""
LLM API를 사용해 문서 타입별(pdf/docx_doc/xlsx_xls_csv/ppt_pptx)로 QA 쌍을 생성한다.
"""
import json
import time
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from config import QA_PER_DOC, OUTPUT_DIR
from llm_client import (
    LLMAPIError,
    LLMClient,
    LLMRateLimitError,
    is_llm_configured,
    required_api_key_name,
)

QA_SYSTEM_PROMPT = """당신은 다양한 형식(PDF/워드/엑셀/파워포인트)의 한국어 문서를 분석해
Cohesity GAIA RAG 시스템 평가용 QA 쌍을 만드는 전문가입니다.

문서 형식별 질문 전략:
- PDF/DOCX(보고서·문서): 핵심 사실·수치·날짜 확인, 목적/내용 요약
- XLSX/XLS/CSV(표 데이터): 특정 항목의 수치, 합계/증감, 표 안의 관계
- PPT/PPTX(발표자료): 슬라이드의 핵심 주장, 제시된 수치·절차 확인

규칙:
1. 질문은 문서의 실제 내용에 근거해야 합니다 (지어내지 마세요).
2. 답변은 문서에서 직접 확인할 수 있는 사실이어야 합니다.
3. 질문과 답변은 모두 한국어로 작성하세요 (문서가 영어여도 질문/답변은 한국어로).
4. 답변은 1-3문장으로 간결하게 작성하세요.
5. 문서 형식에 맞는 질문을 우선 생성하세요 (표 데이터면 수치 질문, 발표자료면 핵심 주장 질문 등).

출력 형식 (JSON 배열):
[
  {
    "question": "질문",
    "answer": "답변",
    "question_type": "factual|summary|numeric|relationship"
  }
]"""

_TYPE_LABEL = {
    "pdf": "PDF 문서",
    "docx_doc": "워드 문서(DOCX/DOC)",
    "xlsx_xls_csv": "엑셀/CSV 표 데이터",
    "ppt_pptx": "파워포인트 발표자료",
}


def build_qa_user_prompt(doc: dict, n_qa: int = QA_PER_DOC) -> str:
    type_group = doc.get("type_group", "")
    label = _TYPE_LABEL.get(type_group, type_group)
    source = doc.get("source", "")
    file_name = doc.get("file_name", "")
    text = doc.get("text", "")

    return f"""다음 {label}에서 QA 쌍을 정확히 {n_qa}개 생성하세요.

문서 종류: {label}
출처: {source}
파일명: {file_name}

문서 내용:
---
{text}
---

JSON 배열 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력하세요."""


def generate_qa_for_doc(
    client: LLMClient,
    doc: dict,
    n_qa: int = QA_PER_DOC,
    max_retries: int = 3,
) -> list[dict]:
    prompt = build_qa_user_prompt(doc, n_qa)

    for attempt in range(max_retries):
        try:
            content = client.generate(QA_SYSTEM_PROMPT, prompt, max_tokens=1024)

            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:])
                if content.endswith("```"):
                    content = content[:-3].strip()

            qa_list = json.loads(content)
            for qa in qa_list:
                qa["doc_id"] = doc.get("doc_id", "")
                qa["type_group"] = doc.get("type_group", "")
                qa["ext"] = doc.get("ext", "")
                qa["source"] = doc.get("source", "")
                qa["file_name"] = doc.get("file_name", "")
                qa["file_path"] = doc.get("file_path", "")
                qa["source_text"] = doc.get("text", "")
            return qa_list

        except json.JSONDecodeError:
            print(f"  [WARN] JSON 파싱 실패 (시도 {attempt+1}/{max_retries})")
            time.sleep(2)
        except LLMRateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  [WARN] Rate limit. {wait}초 대기...")
            time.sleep(wait)
        except LLMAPIError as e:
            print(f"  [ERROR] API 오류: {e}")
            time.sleep(5)

    return []


def generate_all_qa(
    docs: list[dict],
    type_group: str,
    n_qa_per_doc: int = QA_PER_DOC,
    save_intermediate: bool = True,
) -> list[dict]:
    if not is_llm_configured():
        raise ValueError(f"{required_api_key_name()} 환경 변수가 설정되지 않았습니다.")

    output_path = OUTPUT_DIR / f"qa_pairs_{type_group}.json"

    existing_qa = []
    existing_doc_ids = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            existing_qa = json.load(f)
        existing_doc_ids = {qa["doc_id"] for qa in existing_qa}
        print(f"[재개] 기존 {len(existing_qa)}개 QA 로드 완료. 남은 문서 처리 중...")

    client = LLMClient()
    all_qa = list(existing_qa)

    pending_docs = [d for d in docs if d.get("doc_id", "") not in existing_doc_ids]

    for i, doc in enumerate(tqdm(pending_docs, desc=f"QA 생성[{type_group}]", unit="doc")):
        qa_list = generate_qa_for_doc(client, doc, n_qa_per_doc)
        if qa_list:
            all_qa.extend(qa_list)

        if save_intermediate and (i + 1) % 10 == 0:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(all_qa, f, ensure_ascii=False, indent=2)

        time.sleep(0.3)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, ensure_ascii=False, indent=2)

    print(f"[저장] QA 쌍 {len(all_qa)}개 → {output_path}")
    return all_qa


if __name__ == "__main__":
    import argparse
    from config import TYPE_GROUPS
    from document_sampler import load_sampled_documents

    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=list(TYPE_GROUPS.keys()), required=True)
    args = parser.parse_args()

    docs = load_sampled_documents(args.type)
    qa_pairs = generate_all_qa(docs, args.type)
    print(f"\n총 {len(qa_pairs)}개 QA 쌍 생성 완료.")

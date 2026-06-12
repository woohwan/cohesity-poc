"""
Claude API를 사용해 DART 공시 문서에서 QA 쌍을 생성한다.

생성 전략:
  - 사실 확인 질문 (수치, 날짜, 비율 등)
  - 요약 질문 (공시 목적, 주요 내용)
  - 비교/관계 질문 (계열사, 지분 관계 등)
"""
import json
import time
from pathlib import Path
from typing import Optional

import anthropic
from tqdm import tqdm

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, QA_PER_DOC, OUTPUT_DIR, REPORT_TYPE_MAP


QA_SYSTEM_PROMPT = """당신은 한국 금융 공시 문서(DART)를 분석하는 전문가입니다.
주어진 공시 문서 텍스트를 바탕으로 정확하고 구체적인 QA 쌍을 생성하세요.

문서 유형별 질문 전략:
- XML/PDF (공시 전문): 수치·날짜 확인, 공시 목적 요약, 계열사·지분 관계 파악
- XLS (재무제표): 매출액·영업이익·자산 등 재무수치, 전기 대비 증감, 감사의견

규칙:
1. 질문은 문서의 실제 내용에 근거해야 합니다.
2. 답변은 문서에서 직접 확인할 수 있는 사실이어야 합니다.
3. 재무제표 문서의 경우 구체적인 수치(금액, 비율)를 포함한 질문을 우선 생성하세요.
4. 질문과 답변은 모두 한국어로 작성하세요.
5. 답변은 1-3문장으로 간결하게 작성하세요.

출력 형식 (JSON 배열):
[
  {
    "question": "질문",
    "answer": "답변",
    "question_type": "factual|summary|financial|relationship"
  }
]"""


def build_qa_user_prompt(doc: dict, n_qa: int = QA_PER_DOC) -> str:
    company = doc.get("company", "")
    report_type_code = doc.get("report_type", "")
    report_type_name = REPORT_TYPE_MAP.get(report_type_code, report_type_code)
    filing_date = doc.get("filing_date", "")
    source_type = doc.get("source_type", "xml")
    text = doc.get("text", "")

    date_str = f"{filing_date[:4]}-{filing_date[4:6]}-{filing_date[6:8]}" if len(filing_date) == 8 else filing_date
    src_label = {"xml": "XML 전자공시", "pdf": "PDF 원문", "xls": "XLS 재무제표"}.get(source_type, source_type)

    return f"""다음 DART 공시 문서에서 QA 쌍을 정확히 {n_qa}개 생성하세요.

회사명: {company}
보고서 유형: {report_type_name} ({report_type_code})
공시 날짜: {date_str}
문서 형식: {src_label}

문서 내용:
---
{text}
---

JSON 배열 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력하세요."""


def generate_qa_for_doc(
    client: anthropic.Anthropic,
    doc: dict,
    n_qa: int = QA_PER_DOC,
    max_retries: int = 3,
) -> list[dict]:
    """단일 문서에 대한 QA 쌍 생성."""
    prompt = build_qa_user_prompt(doc, n_qa)

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=QA_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text.strip()

            # JSON 파싱 - 코드블록 제거
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:])
                if content.endswith("```"):
                    content = content[:-3].strip()

            qa_list = json.loads(content)
            # 각 QA에 문서 메타데이터 추가
            for qa in qa_list:
                qa["doc_id"] = doc.get("receipt_no", "")
                qa["company"] = doc.get("company", "")
                qa["company_code"] = doc.get("company_code", "")
                qa["report_type"] = doc.get("report_type", "")
                qa["filing_date"] = doc.get("filing_date", "")
                qa["dataset"] = doc.get("dataset", "")
                qa["source_text"] = doc.get("text", "")
            return qa_list

        except json.JSONDecodeError:
            print(f"  [WARN] JSON 파싱 실패 (시도 {attempt+1}/{max_retries})")
            time.sleep(2)
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  [WARN] Rate limit. {wait}초 대기...")
            time.sleep(wait)
        except anthropic.APIError as e:
            print(f"  [ERROR] API 오류: {e}")
            time.sleep(5)

    return []


def generate_all_qa(
    docs: list[dict],
    n_qa_per_doc: int = QA_PER_DOC,
    save_intermediate: bool = True,
    output_path: Optional[Path] = None,
) -> list[dict]:
    """
    모든 문서에 대해 QA 쌍을 생성한다.
    중간 저장을 통해 중단 후 재개가 가능하다.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")

    if output_path is None:
        output_path = OUTPUT_DIR / "qa_pairs.json"

    # 기존 결과 불러오기 (재개용)
    existing_qa = []
    existing_doc_ids = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            existing_qa = json.load(f)
        existing_doc_ids = {qa["doc_id"] for qa in existing_qa}
        print(f"[재개] 기존 {len(existing_qa)}개 QA 로드 완료. 남은 문서 처리 중...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    all_qa = list(existing_qa)

    pending_docs = [d for d in docs if d.get("receipt_no", "") not in existing_doc_ids]

    for i, doc in enumerate(tqdm(pending_docs, desc="QA 생성", unit="doc")):
        qa_list = generate_qa_for_doc(client, doc, n_qa_per_doc)

        if qa_list:
            all_qa.extend(qa_list)

        # 10개마다 중간 저장
        if save_intermediate and (i + 1) % 10 == 0:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(all_qa, f, ensure_ascii=False, indent=2)

        # API 과부하 방지
        time.sleep(0.5)

    # 최종 저장
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, ensure_ascii=False, indent=2)

    print(f"[저장] QA 쌍 {len(all_qa)}개 → {output_path}")
    return all_qa


if __name__ == "__main__":
    from document_sampler import load_sampled_documents

    docs = load_sampled_documents()
    qa_pairs = generate_all_qa(docs)
    print(f"\n총 {len(qa_pairs)}개 QA 쌍 생성 완료.")

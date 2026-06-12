"""
gaia_dataset/ 에서 문서를 샘플링하여 Claude API로 QA 쌍을 생성한다.
qdrant_rag 독립 실행용 — gaia_ragas에 의존하지 않는다.

사용법:
  python qa_gen.py                        전체 100개 샘플
  python qa_gen.py --company 삼성전자     특정 회사 (qa_pairs_삼성전자.json)
  python qa_gen.py --sample 50            샘플 수 조정
  python qa_gen.py --qa-per-doc 3         문서당 QA 수 조정
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# config는 sys.path 조작 전에 import
from config import (
    GAIA_DATASET_DIR, OUTPUT_DIR, ANTHROPIC_API_KEY,
    SAMPLE_SIZE, QA_PER_DOC, MIN_TEXT_LENGTH, MAX_TEXT_LENGTH,
)

try:
    from dart_xml_parser import parse_file
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "gaia_ragas"))
    from dart_xml_parser import parse_file  # noqa: E402

import anthropic

SUPPORTED_EXT = {".xml", ".pdf", ".xls", ".xlsx"}

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


def collect_files(company_filter: Optional[str] = None) -> list[Path]:
    if not GAIA_DATASET_DIR.exists():
        print(f"[ERROR] gaia_dataset 없음: {GAIA_DATASET_DIR}")
        sys.exit(1)
    files = []
    for company_dir in sorted(GAIA_DATASET_DIR.iterdir()):
        if not company_dir.is_dir() or company_dir.name.startswith("_"):
            continue
        if company_filter and company_filter not in company_dir.name:
            continue
        for f in company_dir.iterdir():
            if f.suffix.lower() in SUPPORTED_EXT and not f.name.startswith("_"):
                files.append(f)
    return files


def extract_meta(file_path: Path) -> dict:
    company_dir = file_path.parent.name
    stem = file_path.stem
    parts = stem.rsplit("_", 3)
    return {
        "company_code": company_dir[:6] if len(company_dir) >= 7 else "",
        "company":      company_dir[7:] if len(company_dir) >= 7 else company_dir,
        "filing_date":  parts[1] if len(parts) >= 4 else "",
        "report_name":  parts[2] if len(parts) >= 4 else "",
        "uid":          parts[3] if len(parts) >= 4 else "",
        "source_type":  file_path.suffix.lstrip(".").lower(),
        "file_name":    file_path.name,
    }


def load_docs(files: list[Path], sample_size: int) -> list[dict]:
    random.seed(42)
    sampled = random.sample(files, min(sample_size, len(files)))
    docs = []
    for f in tqdm(sampled, desc="문서 파싱", unit="file"):
        try:
            text, _ = parse_file(f)
            if not text or len(text) < MIN_TEXT_LENGTH:
                continue
            meta = extract_meta(f)
            meta["text"] = text[:MAX_TEXT_LENGTH]
            docs.append(meta)
        except Exception:
            pass
    return docs


def build_prompt(doc: dict, n_qa: int) -> str:
    date = doc.get("filing_date", "")
    date_str = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date
    src_label = {
        "xml":  "XML 전자공시",
        "pdf":  "PDF 원문",
        "xls":  "XLS 재무제표",
        "xlsx": "XLS 재무제표",
    }.get(doc.get("source_type", ""), "공시")

    return f"""다음 DART 공시 문서에서 QA 쌍을 정확히 {n_qa}개 생성하세요.

회사명: {doc.get("company", "")}
보고서: {doc.get("report_name", "")}
공시 날짜: {date_str}
문서 형식: {src_label}

문서 내용:
---
{doc.get("text", "")}
---

JSON 배열 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력하세요."""


def generate_qa(client: anthropic.Anthropic, doc: dict, n_qa: int,
                max_retries: int = 3) -> list[dict]:
    prompt = build_prompt(doc, n_qa)
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=QA_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.content[0].text.strip()
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:])
                if content.endswith("```"):
                    content = content[:-3].strip()
            qa_list = json.loads(content)
            for qa in qa_list:
                qa["doc_id"]      = doc.get("uid", "")
                qa["company"]     = doc.get("company", "")
                qa["company_code"]= doc.get("company_code", "")
                qa["report_name"] = doc.get("report_name", "")
                qa["filing_date"] = doc.get("filing_date", "")
                qa["source_type"] = doc.get("source_type", "")
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


def run(sample_size: int, qa_per_doc: int,
        company_filter: Optional[str] = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY 환경 변수를 설정하세요.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    suffix = f"_{company_filter}" if company_filter else ""
    out_path = OUTPUT_DIR / f"qa_pairs{suffix}.json"

    # 기존 결과 로드 (재개용)
    existing_qa: list[dict] = []
    existing_doc_ids: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing_qa = json.load(f)
        existing_doc_ids = {qa["doc_id"] for qa in existing_qa if qa.get("doc_id")}
        print(f"[재개] 기존 {len(existing_qa)}개 QA 로드. 남은 문서 처리 중...")

    files = collect_files(company_filter)
    print(f"[파일] {len(files):,}개 발견 → {min(sample_size, len(files))}개 샘플링")
    docs = load_docs(files, sample_size)
    pending = [d for d in docs if d.get("uid", "") not in existing_doc_ids]
    print(f"[문서] 파싱 성공: {len(docs)}개 / 미처리: {len(pending)}개")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    all_qa = list(existing_qa)

    for i, doc in enumerate(tqdm(pending, desc="QA 생성", unit="doc")):
        qa_list = generate_qa(client, doc, qa_per_doc)
        if qa_list:
            all_qa.extend(qa_list)
        if (i + 1) % 10 == 0:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(all_qa, f, ensure_ascii=False, indent=2)
        time.sleep(0.5)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, ensure_ascii=False, indent=2)

    print(f"\n[완료] QA 쌍 {len(all_qa)}개 → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gaia_dataset 에서 QA 쌍 생성")
    parser.add_argument("--sample",     type=int, default=SAMPLE_SIZE,
                        help=f"샘플 문서 수 (기본: {SAMPLE_SIZE})")
    parser.add_argument("--qa-per-doc", type=int, default=QA_PER_DOC,
                        help=f"문서당 QA 수 (기본: {QA_PER_DOC})")
    parser.add_argument("--company",    type=str, default=None,
                        help="특정 회사명 필터")
    args = parser.parse_args()
    run(sample_size=args.sample, qa_per_doc=args.qa_per_doc,
        company_filter=args.company)

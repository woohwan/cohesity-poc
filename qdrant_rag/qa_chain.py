"""
Qdrant 검색 + Claude API를 결합한 RAG Q&A 체인

사용법:
  python qa_chain.py "삼성전자의 2021년 매출액은 얼마인가?"
  python qa_chain.py "LG화학 배터리 사업 분할 내용은?" --company LG화학
"""
import argparse
import sys
from pathlib import Path

import anthropic

from config import CLAUDE_MODEL, ANTHROPIC_API_KEY, TOP_K
from retriever import search, SearchResult

SYSTEM_PROMPT = """당신은 한국 상장기업의 공시 문서(사업보고서, 주요사항보고서 등)를 분석하는 전문가입니다.
주어진 참고 문서를 바탕으로 질문에 정확하게 답변하세요.

규칙:
- 참고 문서에 근거한 내용만 답변하세요.
- 문서에 없는 내용은 "제공된 문서에서 찾을 수 없습니다"라고 답하세요.
- 수치, 날짜, 회사명은 정확하게 인용하세요.
- 답변은 한국어로 작성하세요."""


def format_contexts(results: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        header = f"[문서 {i}] {r.company} | {r.report_name} | {r.filing_date} | {r.source_type.upper()}"
        parts.append(f"{header}\n{r.text}")
    return "\n\n---\n\n".join(parts)


def ask(
    question: str,
    top_k: int = TOP_K,
    company_filter: str = None,
    source_type_filter: str = None,
) -> dict:
    """
    질문 → 검색 → Claude 답변.
    반환: {question, answer, contexts, sources}
    """
    results = search(question, top_k=top_k,
                     company_filter=company_filter,
                     source_type_filter=source_type_filter)

    if not results:
        return {
            "question": question,
            "answer": "관련 문서를 찾을 수 없습니다.",
            "contexts": [],
            "sources": [],
        }

    context_text = format_contexts(results)
    user_message = f"참고 문서:\n\n{context_text}\n\n질문: {question}"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer = response.content[0].text

    return {
        "question": question,
        "answer": answer,
        "contexts": [r.text for r in results],
        "sources": [
            {
                "file_name": r.file_name,
                "company": r.company,
                "filing_date": r.filing_date,
                "report_name": r.report_name,
                "source_type": r.source_type,
                "score": round(r.score, 4),
            }
            for r in results
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART RAG Q&A")
    parser.add_argument("question", help="질문")
    parser.add_argument("--top-k",   type=int, default=TOP_K)
    parser.add_argument("--company", type=str, default=None, help="회사명 필터")
    parser.add_argument("--type",    type=str, default=None,
                        choices=["xml", "pdf", "xls", "xlsx"], help="파일 유형 필터")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY 환경 변수를 설정하세요.")
        sys.exit(1)

    result = ask(args.question, top_k=args.top_k,
                 company_filter=args.company, source_type_filter=args.type)

    print(f"\n질문: {result['question']}")
    print(f"\n답변:\n{result['answer']}")
    print(f"\n참조 문서 ({len(result['sources'])}개):")
    for s in result["sources"]:
        print(f"  [{s['score']:.4f}] {s['company']} | {s['report_name']} | {s['filing_date']} | {s['source_type'].upper()}")

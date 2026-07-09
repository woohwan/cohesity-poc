"""
`ragas` 패키지 호환 shim.

ragas>=0.2.0 은 `ragas/llms/base.py`에서 무조건
`from langchain_community.chat_models.vertexai import ChatVertexAI` 를 import한다.
그런데 최신 `langchain-community`(0.4.x)는 그 서브모듈을 이미 제거해서
`ModuleNotFoundError`가 난다 (2026-07-09, ragas 0.2.15~0.4.3 전부 확인된 문제).

`ChatVertexAI`는 ragas 내부에서 `MULTIPLE_COMPLETION_SUPPORTED` 타입 체크 리스트에만
쓰이고 이 프로젝트는 VertexAI를 전혀 쓰지 않으므로(Anthropic Claude만 사용),
동작하는 진짜 구현 대신 존재만 하는 더미 클래스를 `sys.modules`에 등록해
import가 통과되게 한다.

`ragas`를 import하기 전에 반드시 이 모듈을 먼저 import해야 한다:

    import _ragas_compat  # noqa: F401  (ragas import 전에 위치)
    from ragas import evaluate
"""
import sys
import types

_MODULE_NAME = "langchain_community.chat_models.vertexai"

if _MODULE_NAME not in sys.modules:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401  — 이미 있으면 shim 불필요
    except ModuleNotFoundError:
        from langchain_core.language_models.chat_models import BaseChatModel

        class ChatVertexAI(BaseChatModel):  # noqa: N801 — 원본 클래스명과 맞춤
            """Placeholder — 실제 VertexAI 기능 없음. ragas의 isinstance 체크 통과용."""

            @property
            def _llm_type(self) -> str:
                return "vertexai-stub"

            def _generate(self, *args, **kwargs):
                raise NotImplementedError(
                    "ChatVertexAI는 _ragas_compat.py의 더미 shim입니다. "
                    "이 프로젝트에서는 Anthropic Claude만 사용합니다."
                )

        _shim = types.ModuleType(_MODULE_NAME)
        _shim.ChatVertexAI = ChatVertexAI
        sys.modules[_MODULE_NAME] = _shim

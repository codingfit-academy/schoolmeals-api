"""
AI 학교소개 생성 provider
─────────────────────────────────────────────────────────────
실제 LLM 호출부를 AIProvider 인터페이스 뒤로 숨겨서, 나중에 provider를
바꾸더라도 app/services/school_ai.py(캐싱 오케스트레이션)는 건드릴 필요가
없게 한다. 지금은 Claude(Anthropic API)를 기본으로 쓰되, ANTHROPIC_API_KEY가
없으면 StubAIProvider로 자동 대체해 파이프라인 자체는 항상 동작하게 한다.
"""
import logging
from abc import ABC, abstractmethod

import anthropic
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger("school_ai")


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def generate_school_intro(self, school: dict, meals: list[dict]) -> dict:
        """학교/최근 급식 정보를 받아 JSON 직렬화 가능한 소개 컨텐츠를 생성한다."""
        raise NotImplementedError


class StubAIProvider(AIProvider):
    """실제 AI 호출 없이 캐싱 파이프라인(claim → 생성 → 저장 → 캐시 히트)을
    검증하기 위한 더미 provider. AI_PROVIDER=stub 이거나 API 키가 없을 때 사용."""

    name = "stub"

    async def generate_school_intro(self, school: dict, meals: list[dict]) -> dict:
        logger.info("StubAIProvider: 더미 학교소개 생성 - %s", school.get("name"))
        return {
            "intro": f"{school.get('name', '학교')}에 대한 소개입니다. (stub)",
            "highlights": [],
            "mealComment": "급식 데이터가 준비되면 코멘트가 표시됩니다. (stub)",
        }


class _SchoolIntro(BaseModel):
    intro: str
    highlights: list[str]
    meal_comment: str


class ClaudeAIProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate_school_intro(self, school: dict, meals: list[dict]) -> dict:
        meal_lines = "\n".join(
            f"- {m['meal_date']} ({m['meal_type_name']}): {m['menu_text']}"
            for m in meals
        ) or "(최근 급식 정보 없음)"

        prompt = (
            f"학교명: {school.get('name') or '정보 없음'}\n"
            f"종류: {school.get('kind') or '정보 없음'}\n"
            f"주소: {school.get('address') or '정보 없음'}\n\n"
            f"최근 급식 정보:\n{meal_lines}\n\n"
            "위 정보를 바탕으로, 이 학교 페이지를 처음 방문한 학부모/학생에게 보여줄 "
            "짧고 친근한 학교 소개를 작성해줘. 과장하지 말고 사실 위주로."
        )

        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            output_format=_SchoolIntro,
        )
        parsed = response.parsed_output
        return {
            "intro": parsed.intro,
            "highlights": parsed.highlights,
            "mealComment": parsed.meal_comment,
        }


def get_ai_provider() -> AIProvider:
    if settings.ai_provider == "claude":
        if not settings.anthropic_api_key:
            logger.warning(
                "AI_PROVIDER=claude 이지만 ANTHROPIC_API_KEY가 비어있어 StubAIProvider로 대체합니다."
            )
            return StubAIProvider()
        return ClaudeAIProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return StubAIProvider()

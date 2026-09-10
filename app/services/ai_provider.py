"""
AI 학교소개 생성 provider
─────────────────────────────────────────────────────────────
실제 LLM 호출부를 AIProvider 인터페이스 뒤로 숨겨서, 나중에 provider를
바꾸더라도 app/services/school_ai.py(캐싱 오케스트레이션)는 건드릴 필요가
없게 한다. 기본은 Gemini(가장 저렴한 flash-lite 계열)이고, Claude도 그대로
선택할 수 있다. API 키가 없으면 StubAIProvider로 자동 대체해 파이프라인
자체는 항상 동작하게 한다.

AI 호출 자체는 학교당 최초 1회만 일어난다 — school_ai.py가 결과를 DB에
캐시하고 이후 접속자에게는 저장된 값을 그대로 내려준다.
"""
import logging
from abc import ABC, abstractmethod

import anthropic
from google import genai
from google.genai import types
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


def build_prompt(school: dict, meals: list[dict]) -> str:
    """provider와 무관한 학교소개 생성 프롬프트."""
    meal_lines = "\n".join(
        f"- {m['meal_date']} ({m['meal_type_name']}): {m['menu_text']}"
        for m in meals
    ) or "(최근 급식 정보 없음)"

    return (
        f"학교명: {school.get('name') or '정보 없음'}\n"
        f"종류: {school.get('kind') or '정보 없음'}\n"
        f"주소: {school.get('address') or '정보 없음'}\n\n"
        f"최근 급식 정보:\n{meal_lines}\n\n"
        "위 정보를 바탕으로, 이 학교 페이지를 처음 방문한 학부모/학생에게 보여줄 "
        "짧고 친근한 학교 소개를 작성해줘. 과장하지 말고 사실 위주로."
    )


class GeminiAIProvider(AIProvider):
    """Google Gemini — flash-lite 계열을 쓰면 토큰 단가가 가장 저렴하다."""

    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate_school_intro(self, school: dict, meals: list[dict]) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=build_prompt(school, meals),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SchoolIntro,
            ),
        )
        parsed: _SchoolIntro = response.parsed
        return {
            "intro": parsed.intro,
            "highlights": parsed.highlights,
            "mealComment": parsed.meal_comment,
        }


class ClaudeAIProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate_school_intro(self, school: dict, meals: list[dict]) -> dict:
        prompt = build_prompt(school, meals)

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
    if settings.ai_provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning(
                "AI_PROVIDER=gemini 이지만 GEMINI_API_KEY가 비어있어 StubAIProvider로 대체합니다."
            )
            return StubAIProvider()
        return GeminiAIProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)

    if settings.ai_provider == "claude":
        if not settings.anthropic_api_key:
            logger.warning(
                "AI_PROVIDER=claude 이지만 ANTHROPIC_API_KEY가 비어있어 StubAIProvider로 대체합니다."
            )
            return StubAIProvider()
        return ClaudeAIProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return StubAIProvider()

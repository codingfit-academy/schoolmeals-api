"""
AI 메뉴 분석 provider
─────────────────────────────────────────────────────────────
실제 LLM 호출부를 AIProvider 인터페이스 뒤로 숨겨서, 나중에 provider를
바꾸더라도 app/services/menu_insights.py(캐싱 오케스트레이션)는 건드릴 필요가
없게 한다. 기본은 Gemini(가장 저렴한 flash-lite 계열)이고, Claude도 그대로
선택할 수 있다. API 키가 없으면 StubAIProvider로 자동 대체해 파이프라인
자체는 항상 동작하게 한다.

AI 호출 자체는 학교×날짜당 최초 1회만 일어난다 — menu_insights.py가 결과를
DB에 캐시하고 이후 접속자에게는 저장된 값을 그대로 내려준다.

이 프로젝트에서 AI를 호출하는 경로는 analyze_menu()(/menu 페이지)와
suggest_allergens()(/calendar 페이지) 둘뿐이다. 예전에는 학교 소개 문구를
생성하는 generate_school_intro()도 있었지만, /menu 페이지가 메뉴 분석으로
대체되면서 관련 라우터·서비스·모델과 함께 완전히 제거했다 (school_ai.py,
SchoolAiContent, GET /schools/{office_code}/{school_code}/ai).
"""
import logging
from abc import ABC, abstractmethod
from typing import Literal

import anthropic
from google import genai
from google.genai import types
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger("ai_provider")


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def analyze_menu(self, dishes: list[str]) -> dict:
        """오늘 급식 메뉴를 한 번의 호출로 분석해 먹는 팁 / 건강 포인트 / 영양 밸런스 /
        유튜브 검색어를 함께 반환한다. 네 가지를 한 호출로 묶는 이유는 AI 호출 비용을 최소화하기 위함이다.
        반환 형태: {
            "eatingTip": {"dish": str, "tip": str} | None,
            "healthNotes": [{"bodyPart": str, "note": str}],
            "balance": {"score": int, "summary": str, "groups": [{"group": str, "level": str}]},
            "searchKeywords": [{"dish": str, "keyword": str}],
        }
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_eating_methods(self, dish_videos: list[dict]) -> dict:
        """그 날 메뉴로 찾은 유튜브 먹방 영상들(제목·채널·설명)을 읽고, 사람들이 실제로 어떻게
        먹는지를 정리한다. 여러 영상에서 반복되는 방법일수록 많이 먹는 방법이므로 가장 앞에 세운다.
        입력: [{"dish": str, "videos": [{"title": str, "channelTitle": str, "description": str}]}]
        반환: {"summary": str, "topMethod": {"dish", "method", "howTo", "why"} | None,
               "methods": [{"dish", "method", "howTo", "why"}]}
        """
        raise NotImplementedError

    @abstractmethod
    async def suggest_allergens(self, days: list[dict]) -> dict:
        """NEIS 공식 알레르기 코드에 빠졌을 수 있는 성분을, 메뉴 이름만으로 확실히 알 수
        있는 경우에 한해 날짜별로 추정한다 (참고용 — 공식 코드를 대체하지 않음).
        조금이라도 애매하면 포함하지 않도록 프롬프트에서 강하게 제한한다.
        입력: [{"date": "YYYYMMDD", "dishes": [{"name": str, "knownAllergens": list[str]}]}]
        반환: {"YYYYMMDD": [{"dish": str, "allergen": str, "reason": str}]}
        """
        raise NotImplementedError


class StubAIProvider(AIProvider):
    """실제 AI 호출 없이 캐싱 파이프라인(claim → 생성 → 저장 → 캐시 히트)을
    검증하기 위한 더미 provider. AI_PROVIDER=stub 이거나 API 키가 없을 때 사용."""

    name = "stub"

    async def analyze_menu(self, dishes: list[str]) -> dict:
        logger.info("StubAIProvider: 더미 메뉴 분석 - %s", dishes)
        # 검색어는 메뉴 이름을 그대로 쓰는 것이 AI 없는 상태의 자연스러운 폴백이다.
        return {
            "eatingTip": None,
            "healthNotes": [],
            "balance": None,
            "searchKeywords": [{"dish": d, "keyword": d} for d in dishes],
        }

    async def summarize_eating_methods(self, dish_videos: list[dict]) -> dict:
        logger.info("StubAIProvider: 더미 식사법 요약 - %d개 메뉴", len(dish_videos))
        return {"summary": "", "topMethod": None, "methods": []}

    async def suggest_allergens(self, days: list[dict]) -> dict:
        logger.info("StubAIProvider: 더미 알레르기 보완 - %d일", len(days))
        return {d["date"]: [] for d in days}


class _EatingTip(BaseModel):
    dish: str
    tip: str


# 프론트(TodayMenuPage)가 인체 그림 위 고정된 위치에 점을 찍어 보여주므로,
# AI가 자유 텍스트 대신 이 목록 중에서만 고르게 제한한다.
BODY_PARTS: tuple[str, ...] = (
    "뇌", "눈", "목", "심장", "폐", "위장", "장", "근육", "뼈", "혈액", "피부", "면역력", "전신",
)


class _HealthNote(BaseModel):
    body_part: Literal[BODY_PARTS]
    note: str


# 영양 밸런스 카드에 쓰는 고정 값들 — 프론트가 색/아이콘을 매핑할 수 있게 자유 텍스트를 막는다.
BALANCE_GROUPS: tuple[str, ...] = ("탄수화물", "단백질", "채소", "칼슘", "비타민")
BALANCE_LEVELS: tuple[str, ...] = ("충분", "보통", "부족")


class _BalanceGroup(BaseModel):
    group: Literal[BALANCE_GROUPS]
    level: Literal[BALANCE_LEVELS]


class _Balance(BaseModel):
    score: int
    summary: str
    groups: list[_BalanceGroup]


class _DishKeyword(BaseModel):
    dish: str
    keyword: str


class _MenuInsights(BaseModel):
    eating_tip: _EatingTip | None
    health_notes: list[_HealthNote]
    balance: _Balance
    search_keywords: list[_DishKeyword]


# 프론트(utils/parseMeal.js ALLERGENS)와 동일한 19개 NEIS 표준 알레르기 유발식품 키.
# AI가 이 목록 밖의 값을 만들어내면 프론트가 매핑할 라벨이 없으므로 반드시 이 안에서만 고르게 한다.
ALLERGEN_KEYS: tuple[str, ...] = (
    "egg", "dairy", "buckwheat", "peanut", "soy", "wheat", "mackerel", "crab", "shrimp",
    "pork", "peach", "tomato", "sulfite", "walnut", "chicken", "beef", "squid", "shellfish", "pinenut",
)


class _EatingMethod(BaseModel):
    dish: str
    method: str
    how_to: str
    why: str


class _EatingMethods(BaseModel):
    summary: str
    top_method: _EatingMethod | None
    methods: list[_EatingMethod]


class _AllergenGuess(BaseModel):
    dish: str
    allergen: Literal[ALLERGEN_KEYS]
    reason: str


class _DayAllergenGuesses(BaseModel):
    date: str
    guesses: list[_AllergenGuess]


class _AllergenNotes(BaseModel):
    days: list[_DayAllergenGuesses]


def build_menu_insights_prompt(dishes: list[str]) -> str:
    """provider와 무관한 '오늘의 메뉴 분석' 프롬프트 — 먹는 팁 / 건강 포인트 /
    영양 밸런스 / 유튜브 검색어를 한 번에 요청한다 (호출 1회로 묶기 위함)."""
    dish_lines = "\n".join(f"- {d}" for d in dishes)
    return (
        "오늘 학교 급식 메뉴는 다음과 같아:\n"
        f"{dish_lines}\n\n"
        "아래 네 가지를 알려줘.\n\n"
        "1) eatingTip: 이 메뉴들을 더 맛있게 먹는 아주 구체적이고 실용적인 조합이나 방법이 하나 있다면 "
        "(예: 'OO에 마요네즈를 살짝 곁들이면 더 맛있어요') 알려줘. 정말 괜찮은 게 떠오르지 않으면 "
        "절대 억지로 만들지 말고 eatingTip을 null로 둬.\n\n"
        "2) healthNotes: 이 급식이 학생 몸에 어떻게 도움이 되는지 1~4개만 아주 간단하게 알려줘 "
        "(예: '뼈: 칼슘이 들어있어 뼈 건강에 도움을 줘요'). 영양학적으로 엄밀할 필요는 없고, 학생이 "
        "이해하기 쉬운 수준으로 짧게 적어줘.\n\n"
        "3) balance: 오늘 급식 한 끼의 영양 균형을 0~100점 사이 점수(score)와 초등학생도 이해할 수 있는 "
        "한 줄 평(summary)으로 알려줘. groups에는 탄수화물/단백질/채소/칼슘/비타민 중 이 급식에서 판단할 수 "
        "있는 것만 골라 '충분/보통/부족' 중 하나로 표시해줘. 점수는 후하게 주지 말고 실제 구성에 맞게 매겨줘.\n\n"
        "4) searchKeywords: 각 메뉴를 유튜브에서 '먹방' 영상으로 검색할 때 쓸 짧은 검색어를 메뉴마다 하나씩 "
        "알려줘. 급식 표기에는 학교식 줄임말이나 '-', 괄호가 섞여 있으니(예: '포크타코또띠아롤-' → '타코', "
        "'달걀찜(실파)-' → '달걀찜', '친환경쌀밥' → '쌀밥') 사람들이 유튜브에서 실제로 검색할 법한 음식 "
        "이름으로 다듬어줘. 검색어에 '먹방'은 붙이지 말고 음식 이름만 적고, dish에는 위 메뉴 목록의 표기를 "
        "그대로 써줘."
    )


def build_eating_methods_prompt(dish_videos: list[dict]) -> str:
    """provider와 무관한 '유튜버들이 가장 추천하는 식사법' 프롬프트.

    유튜브 영상의 제목·채널·설명만 근거로 삼는다(영상 내용을 직접 보는 것이 아니므로,
    근거가 부족하면 지어내지 말라고 강하게 제한한다). 여러 영상에서 반복되는 방법일수록
    실제로 많이 먹는 방법이라는 점을 명시해 topMethod를 고르게 한다."""
    blocks = []
    for item in dish_videos:
        blocks.append(f"[{item['dish']}]")
        for video in item["videos"]:
            blocks.append(f"- 제목: {video.get('title', '')}")
            channel = video.get("channelTitle")
            if channel:
                blocks.append(f"  채널: {channel}")
            description = (video.get("description") or "").strip().replace("\n", " ")
            if description:
                blocks.append(f"  설명: {description[:300]}")
    listing = "\n".join(blocks)

    return (
        "아래는 오늘 학교 급식 메뉴별로 유튜브에서 찾은 먹방 영상들의 제목·채널·설명이야.\n\n"
        f"{listing}\n\n"
        "이 영상들을 근거로 사람들이 이 음식들을 '어떻게 먹는지' 정리해줘.\n\n"
        "1) summary: 영상들을 전체적으로 봤을 때 사람들이 이 급식 메뉴들을 어떻게 즐겨 먹는지 "
        "두 문장 이내로 요약해줘.\n\n"
        "2) topMethod: 여러 영상에서 반복해서 나오는 먹는 방법이 있다면 그게 사람들이 가장 많이 "
        "먹는 방법이야. 그중 학생이 급식에서 따라 할 수 있는 걸 하나만 골라줘. "
        "method는 '밥에 비벼 먹기'처럼 짧은 이름, howTo는 따라 하는 방법을 한두 문장으로, "
        "why는 왜 이렇게 먹는지(여러 영상에서 반복된다는 점 포함)를 한 문장으로 써줘. "
        "반복되는 방법이 전혀 없으면 억지로 만들지 말고 topMethod를 null로 둬.\n\n"
        "3) methods: 그 밖에 영상에서 확인되는 먹는 방법을 최대 3개까지 같은 형식으로 알려줘. "
        "확실하지 않으면 개수를 줄여도 되고, 없으면 빈 배열로 둬.\n\n"
        "중요: 너는 영상의 제목·설명만 볼 수 있고 실제 영상 내용은 볼 수 없어. 근거가 없는 "
        "내용은 절대 지어내지 말고, dish에는 위 목록에 있는 메뉴 이름을 그대로 써줘. "
        "급식에서 구하기 어려운 재료(예: 특별한 소스, 비싼 재료)는 추천하지 말고, 학교에서 "
        "받은 급식만으로 따라 할 수 있는 방법을 우선해줘."
    )


def build_allergen_notes_prompt(days: list[dict]) -> str:
    """provider와 무관한 '알레르기 보완' 프롬프트 — 날짜별 메뉴와 이미 표시된 공식
    알레르기 코드를 함께 보여주고, 메뉴 이름만으로 확실히 알 수 있는데 빠진 성분만
    추가로 알려달라고 요청한다. 애매하면 절대 추측하지 말라고 강하게 제한한다."""
    lines = []
    for d in days:
        lines.append(f"[{d['date']}]")
        for dish in d["dishes"]:
            known = ", ".join(dish["knownAllergens"]) or "없음"
            lines.append(f"- {dish['name']} (이미 표시된 알레르기: {known})")
    days_block = "\n".join(lines)
    allergen_list = ", ".join(ALLERGEN_KEYS)

    return (
        "다음은 날짜별 학교 급식 메뉴와, 이미 공식적으로 표시된 알레르기 성분이야:\n\n"
        f"{days_block}\n\n"
        "메뉴 이름 자체에서 실제로 들어있는 게 명확한데도 '이미 표시된 알레르기'에 빠져있는 "
        "성분이 있으면 알려줘 (예: 생선 이름이 메뉴에 그대로 들어간 요리인데 생선류 표시가 "
        "없는 경우). 반드시 메뉴 이름에서 명확히 드러나는 경우에만 답해줘 — 조금이라도 "
        "애매하거나 확실하지 않으면 절대 추측하지 말고 답에 포함하지 마.\n\n"
        "중요: 답하기 전에 그 메뉴의 '이미 표시된 알레르기' 목록을 다시 한번 확인해. 네가 "
        "추가하려는 성분이 그 목록에 이미 있다면 절대 답에 포함하지 마 — 이미 표시된 성분을 "
        "다시 알려주는 건 틀린 답이야. 정말로 목록에 없는 성분만 새로 추가해.\n\n"
        f"알레르기 성분은 반드시 이 목록 중에서만 골라: {allergen_list}\n\n"
        "놓친 게 없는 날짜나 메뉴는 그냥 답에서 빼도 돼. 확실한 게 하나도 없으면 days를 "
        "빈 배열로 반환해도 괜찮아."
    )


class GeminiAIProvider(AIProvider):
    """Google Gemini — flash-lite 계열을 쓰면 토큰 단가가 가장 저렴하다."""

    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def analyze_menu(self, dishes: list[str]) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=build_menu_insights_prompt(dishes),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_MenuInsights,
            ),
        )
        parsed: _MenuInsights = response.parsed
        return _serialize_menu_insights(parsed, dishes)

    async def summarize_eating_methods(self, dish_videos: list[dict]) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=build_eating_methods_prompt(dish_videos),
            config={
                "response_mime_type": "application/json",
                "response_schema": _EatingMethods,
            },
        )
        parsed: _EatingMethods = response.parsed
        return _serialize_eating_methods(parsed, dish_videos)

    async def suggest_allergens(self, days: list[dict]) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=build_allergen_notes_prompt(days),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_AllergenNotes,
            ),
        )
        parsed: _AllergenNotes = response.parsed
        return _serialize_allergen_notes(parsed, days)


class ClaudeAIProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def analyze_menu(self, dishes: list[str]) -> dict:
        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": build_menu_insights_prompt(dishes)}],
            output_format=_MenuInsights,
        )
        parsed: _MenuInsights = response.parsed_output
        return _serialize_menu_insights(parsed, dishes)

    async def summarize_eating_methods(self, dish_videos: list[dict]) -> dict:
        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": build_eating_methods_prompt(dish_videos)}],
            output_format=_EatingMethods,
        )
        parsed: _EatingMethods = response.parsed_output
        return _serialize_eating_methods(parsed, dish_videos)

    async def suggest_allergens(self, days: list[dict]) -> dict:
        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": build_allergen_notes_prompt(days)}],
            output_format=_AllergenNotes,
        )
        parsed: _AllergenNotes = response.parsed_output
        return _serialize_allergen_notes(parsed, days)


def _serialize_menu_insights(parsed: "_MenuInsights", dishes: list[str]) -> dict:
    eating_tip = (
        {"dish": parsed.eating_tip.dish, "tip": parsed.eating_tip.tip}
        if parsed.eating_tip
        else None
    )
    health_notes = [{"bodyPart": n.body_part, "note": n.note} for n in parsed.health_notes]

    balance = {
        "score": max(0, min(100, parsed.balance.score)),
        "summary": parsed.balance.summary,
        "groups": [{"group": g.group, "level": g.level} for g in parsed.balance.groups],
    }

    # 목록에 없는 메뉴로 검색어를 만들어오면 프론트가 매칭할 수 없으므로 버리고,
    # 빠진 메뉴는 프론트가 원래 메뉴 이름으로 검색하도록 둔다.
    known_dishes = set(dishes)
    search_keywords = [
        {"dish": k.dish, "keyword": k.keyword.strip()}
        for k in parsed.search_keywords
        if k.dish in known_dishes and k.keyword.strip()
    ]

    return {
        "eatingTip": eating_tip,
        "healthNotes": health_notes,
        "balance": balance,
        "searchKeywords": search_keywords,
    }


def _serialize_eating_methods(parsed: "_EatingMethods", dish_videos: list[dict]) -> dict:
    known_dishes = {item["dish"] for item in dish_videos}

    def to_dict(method: "_EatingMethod | None") -> dict | None:
        # 목록에 없는 메뉴를 지어냈으면 프론트가 연결할 곳이 없으므로 버린다.
        if method is None or method.dish not in known_dishes:
            return None
        return {"dish": method.dish, "method": method.method, "howTo": method.how_to, "why": method.why}

    methods = [m for m in (to_dict(x) for x in parsed.methods) if m]
    return {"summary": parsed.summary, "topMethod": to_dict(parsed.top_method), "methods": methods}


def _serialize_allergen_notes(parsed: "_AllergenNotes", days: list[dict]) -> dict:
    valid_dates = {d["date"] for d in days}
    # (날짜, 메뉴명)별로 이미 표시돼 있던 알레르기 집합을 만들어 둔다 — 프롬프트가
    # "이미 있는 건 다시 답하지 마"라고 명시해도 모델이 종종 어기므로, 서버 쪽에서
    # 그 메뉴 자신의 기존 표시와 겹치는 결과만 한 번 더 걸러낸다 (다른 메뉴의 표시와는
    # 비교하지 않는다 — 같은 날 다른 메뉴에 있다고 이 메뉴의 새로운 발견을 지우면 안 되므로).
    known_by_dish: dict[tuple[str, str], set[str]] = {
        (d["date"], dish["name"]): set(dish["knownAllergens"]) for d in days for dish in d["dishes"]
    }

    result: dict[str, list[dict]] = {d["date"]: [] for d in days}
    for day in parsed.days:
        if day.date not in valid_dates:
            logger.warning("AI가 요청 범위 밖의 날짜를 반환해 무시합니다: %s", day.date)
            continue
        result[day.date] = [
            {"dish": g.dish, "allergen": g.allergen, "reason": g.reason}
            for g in day.guesses
            if g.allergen not in known_by_dish.get((day.date, g.dish), set())
        ]
    return result


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

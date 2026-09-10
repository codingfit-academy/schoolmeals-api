"""
프로젝트 소개 라우터 (AI 경진대회 제출 자료)
─────────────────────────────────────────────────────────────
이 프로젝트에서 어떤 AI를 어떻게 썼는지, 어떤 프롬프트를 넣었는지,
어떤 스펙과 기능으로 구성돼 있는지를 한곳에 정리한 자료입니다.

※ app/main.py에 include_router 하지 않았습니다 (의도적으로 미연결).
   실제로 서비스하려면 main.py에 아래 한 줄을 추가하면 됩니다.

       app.include_router(project_intro.router, tags=["project-intro"])
"""
from fastapi import APIRouter

from ..config import settings
from ..services.ai_provider import build_prompt

router = APIRouter()


PROJECT = {
    "name": "우리 학교 급식",
    "tagline": "매점 음식보다 급식이 더 맛있어요",
    "description": (
        "전국 학교의 실제 급식 데이터(NEIS)를 받아와 오늘의 식단, 알레르기 달력, "
        "메뉴 투표, 급식 게임, 먹방 영상을 한곳에서 보여주는 학생용 웹 서비스입니다. "
        "학교 소개 문구는 AI가 학교별로 한 번 생성해 저장하고, 이후 방문자에게는 "
        "저장된 결과를 그대로 보여줍니다."
    ),
    "repositories": {
        "frontend": "schoolmeals-front (React 18 + Vite)",
        "backend": "schoolmeals-api (FastAPI + PostgreSQL)",
    },
}


# ── 1. 어떤 AI를 사용했는가 ─────────────────────────────────
AI_USAGE = {
    "provider": "Google Gemini",
    "sdk": "google-genai (Google 공식 Gen AI SDK)",
    "defaultModel": settings.gemini_model,
    "modelSelectionReason": (
        "학교 소개 문구 생성은 짧은 텍스트 한 편을 만드는 단순 작업이라 "
        "최상위 모델이 필요하지 않습니다. 토큰 단가가 가장 낮은 flash-lite 계열을 "
        "선택해 비용을 최소화했습니다."
    ),
    "purpose": "학교별 소개 문구 + 급식 코멘트 생성 (app/services/ai_provider.py)",
    "structuredOutput": {
        "method": "response_mime_type='application/json' + Pydantic response_schema",
        "schema": {
            "intro": "str — 학교 소개 본문",
            "highlights": "list[str] — 학교의 특징 목록",
            "meal_comment": "str — 최근 급식에 대한 한마디",
        },
        "reason": "JSON 스키마를 강제해 파싱 실패 없이 그대로 DB에 저장할 수 있게 했습니다.",
    },
    "callPolicy": {
        "when": "사용자가 해당 학교의 급식 페이지에 처음 들어왔을 때",
        "frequency": "학교당 정확히 1회",
        "cache": "school_ai_contents 테이블 (office_code + school_code 유니크)",
        "afterFirstCall": "이후 접속자에게는 AI 호출 없이 DB에 저장된 값을 그대로 반환",
        "concurrency": (
            "INSERT ... ON CONFLICT DO NOTHING 으로 status='pending' 행을 선점(claim)해, "
            "여러 명이 동시에 같은 학교에 들어와도 AI는 한 번만 호출됩니다. "
            "선점하지 못한 요청은 done이 될 때까지 짧게 폴링합니다. "
            "(app/services/school_ai.py)"
        ),
        "failure": "실패하면 status='failed'로 남기고 다음 요청이 재시도합니다.",
    },
    "fallback": (
        "GEMINI_API_KEY가 비어 있으면 StubAIProvider(더미 응답)로 자동 대체돼, "
        "키 없이도 전체 파이프라인이 동작합니다."
    ),
    "swappable": (
        "AIProvider 추상 클래스 뒤에 구현을 숨겨서 AI_PROVIDER 환경변수만 바꾸면 "
        "gemini / claude / stub 으로 교체됩니다. 오케스트레이션 코드는 그대로입니다."
    ),
    "costControls": [
        "학교당 1회만 호출하고 결과를 DB에 영구 캐시",
        "가장 저렴한 flash-lite 계열 모델 사용",
        "출력 스키마를 고정해 재시도/재생성 낭비 방지",
        "급식 한 건마다 호출하지 않고, 학교 단위로만 호출",
    ],
}


# ── 2. 어떤 프롬프트를 사용했는가 ───────────────────────────
_SAMPLE_SCHOOL = {
    "name": "가락고등학교",
    "kind": "고등학교",
    "address": "서울특별시 송파구 송이로 42",
}

_SAMPLE_MEALS = [
    {"meal_date": "2026-09-01", "meal_type_name": "중식", "menu_text": "발아현미밥/미역국/제육볶음/배추김치"},
    {"meal_date": "2026-09-02", "meal_type_name": "중식", "menu_text": "찰보리밥/된장찌개/돈까스/단무지"},
]

PROMPTS = {
    "schoolIntro": {
        "usedBy": "GET /schools/{office_code}/{school_code}/ai",
        "builder": "app/services/ai_provider.py 의 build_prompt()",
        "inputs": [
            "학교명 / 학교 종류 / 주소 (NEIS 학교기본정보)",
            "최근 급식 최대 10건 (날짜, 끼니, 메뉴 텍스트 — NEIS 급식식단정보)",
        ],
        "template": (
            "학교명: {학교명}\n"
            "종류: {학교 종류}\n"
            "주소: {주소}\n\n"
            "최근 급식 정보:\n{날짜 (끼니): 메뉴 목록}\n\n"
            "위 정보를 바탕으로, 이 학교 페이지를 처음 방문한 학부모/학생에게 보여줄 "
            "짧고 친근한 학교 소개를 작성해줘. 과장하지 말고 사실 위주로."
        ),
        # 실제 코드가 만드는 프롬프트를 그대로 렌더링한 예시 (문서와 코드가 어긋나지 않게)
        "renderedExample": build_prompt(_SAMPLE_SCHOOL, _SAMPLE_MEALS),
        "designNotes": [
            "'과장하지 말고 사실 위주로' — 학생 대상 서비스라 없는 사실을 지어내지 않게 제한",
            "'짧고 친근한' — 랜딩 카드에 들어갈 분량으로 길이를 통제",
            "실제 급식 메뉴를 함께 넣어, 학교마다 다른 구체적인 코멘트가 나오게 유도",
            "출력은 JSON 스키마로 고정해 화면 구성 요소(소개/특징/급식 코멘트)에 바로 매핑",
        ],
    },
}


# ── 3. 스펙 ────────────────────────────────────────────────
TECH_SPEC = {
    "frontend": {
        "framework": "React 18 + Vite 5",
        "routing": "react-router-dom 7",
        "styling": "CSS Modules (라이트/다크 테마 토큰)",
        "font": "국민대학교 성곡 세리프 (KMU80 Sungkok Serif, 웹폰트 self-host)",
        "state": "React state + Context(선택한 학교) + localStorage(중복 투표/찜 방지)",
    },
    "backend": {
        "framework": "FastAPI",
        "db": "PostgreSQL (SQLAlchemy 2.0 async + asyncpg)",
        "http": "httpx (NEIS / YouTube 호출)",
        "ai": "google-genai (Gemini), anthropic (선택형 대체 provider)",
    },
    "externalApis": {
        "NEIS 교육정보 개방포털": {
            "endpoints": ["schoolInfo (학교기본정보)", "mealServiceDietInfo (급식식단정보)"],
            "usage": "학교 목록과 실제 급식 식단·영양·원산지 정보",
        },
        "YouTube Data API v3": {
            "endpoints": ["search.list (영상 검색)", "videos.list (재생시간·조회수 보강)"],
            "usage": "급식 메뉴별 먹방 영상 카드",
            "quotaNote": "search.list는 1회 100유닛(무료 일 10,000유닛)이라 검색어당 1회만 호출",
        },
        "Google Gemini API": {
            "usage": "학교 소개 문구 생성",
            "quotaNote": "학교당 1회만 호출",
        },
    },
    "tables": {
        "schools": "학교 기본정보 (사용자가 실제로 연 학교만 저장)",
        "meals": "급식 식단 원본 (NEIS row 그대로 보관)",
        "meal_ingest_states": "학교별로 어느 기간까지 가져왔는지 기록",
        "school_ai_contents": "학교별 AI 소개 캐시 (pending/done/failed)",
        "youtube_caches": "검색어별 유튜브 결과 캐시",
        "food_likes": "급식 메뉴 찜 수 (YYYY-MM 단위)",
        "menu_votes": "메뉴 투표 수 (ISO 주 단위)",
    },
    "externalCallPolicy": {
        "principle": "외부 호출은 '최초 1회 → DB 저장 → 이후 접속자는 DB 값' 원칙으로 통일",
        "rules": [
            {"대상": "AI 학교소개", "호출 시점": "해당 학교 급식 페이지 최초 진입", "재호출": "없음"},
            {"대상": "유튜브 영상", "호출 시점": "해당 검색어 최초 조회", "재호출": "없음"},
            {"대상": "급식 데이터", "호출 시점": "요청 기간이 이미 가져온 구간을 벗어날 때만", "재호출": "구간 밖일 때만"},
            {"대상": "학교 정보", "호출 시점": "급식을 새로 가져와야 하는데 학교 행이 없을 때만", "재호출": "없음"},
        ],
        "removed": (
            "매일 새벽 서울·경기 전체 학교를 통째로 수집하던 배치를 제거하고, "
            "사용자가 실제로 연 학교·기간만 가져오는 지연(lazy) 수집으로 교체했습니다."
        ),
    },
}


# ── 4. 기능 ────────────────────────────────────────────────
FEATURES = [
    {
        "name": "학교 선택",
        "route": "/",
        "description": "서울·경기 학교를 지역 탭과 이름 검색으로 고르고, 선택한 학교를 브라우저에 저장합니다.",
        "dataSource": "NEIS 학교기본정보",
    },
    {
        "name": "오늘의 식단 + AI 학교 소개",
        "route": "/menu",
        "description": (
            "선택한 학교의 오늘 급식(메뉴·알레르기·총 칼로리)을 보여주고, "
            "그 아래에 AI가 만든 학교 소개와 급식 코멘트를 함께 보여줍니다."
        ),
        "dataSource": "NEIS 급식식단정보 + Gemini(학교당 1회 생성 후 캐시)",
        "usesAI": True,
    },
    {
        "name": "알레르기 체크 달력",
        "route": "/calendar",
        "description": (
            "한 달치 급식을 달력으로 보여주고, 알레르기 항목을 고르면 해당 성분이 든 날을 "
            "빨간색으로 표시합니다. 날짜를 누르면 메뉴·영양정보·원산지를 볼 수 있습니다."
        ),
        "dataSource": "NEIS 급식식단정보 (18종 알레르기 코드 파싱)",
    },
    {
        "name": "급식 게임 (학교폭력 예방 퀴즈)",
        "route": "/game",
        "description": (
            "OX퀴즈·초성퀴즈·주관식·단어맞추기 네 가지 유형으로 학교폭력 예방 내용을 "
            "퀴즈로 풀고 점수를 확인합니다. 전체 랜덤 모드도 있습니다."
        ),
        "dataSource": "직접 작성한 문항 데이터",
    },
    {
        "name": "메뉴 투표",
        "route": "/vote",
        "description": (
            "두 가지 급식 먹는 방법 중 하나에 투표합니다. 각 선택지에는 실제 유튜브 먹방 "
            "영상이 붙고, 투표 수는 서버에 저장되어 이번 주 기준으로 집계됩니다."
        ),
        "dataSource": "YouTube Data API v3 + menu_votes 테이블 (ISO 주 단위)",
    },
    {
        "name": "이번 달 BEST 급식 / 음식 상세",
        "route": "/ 및 /food/{slug}",
        "description": (
            "찜을 많이 받은 순서로 이번 달 BEST 급식 순위를 보여주고, 상세 페이지에서 "
            "실제 유튜브 먹방 영상, 맛있게 먹는 법, 집에서 만드는 레시피를 제공합니다. "
            "찜 버튼을 누르면 서버 카운터가 올라갑니다."
        ),
        "dataSource": "YouTube Data API v3 + food_likes 테이블 (월 단위)",
    },
]


@router.get("/project-intro")
async def project_intro():
    """프로젝트 전체 소개 (AI 사용 내역 + 프롬프트 + 스펙 + 기능)."""
    return {
        "project": PROJECT,
        "ai": AI_USAGE,
        "prompts": PROMPTS,
        "spec": TECH_SPEC,
        "features": FEATURES,
    }


@router.get("/project-intro/ai")
async def project_intro_ai():
    """AI 사용 내역과 프롬프트만 따로 보고 싶을 때."""
    return {"ai": AI_USAGE, "prompts": PROMPTS}

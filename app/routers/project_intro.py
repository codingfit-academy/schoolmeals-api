"""
프로젝트 소개 라우터 (AI 경진대회 제출 자료)
─────────────────────────────────────────────────────────────
이 프로젝트에서 어떤 AI를 어떻게 썼는지, 어떤 프롬프트를 넣었는지,
어떤 스펙과 기능으로 구성돼 있는지를 한곳에 정리한 자료입니다.
app/main.py에 include_router 되어 있어 GET /project-intro 로 바로 조회할 수 있습니다.

프롬프트나 AI 사용 방식이 바뀌면 이 파일(PROMPTS, AI_USAGE, TECH_SPEC, FEATURES)도
함께 업데이트해야 실제 코드와 이 문서가 어긋나지 않습니다.
"""
from fastapi import APIRouter

from ..config import settings
from ..services.ai_provider import build_allergen_notes_prompt, build_menu_insights_prompt

router = APIRouter()


PROJECT = {
    "name": "우리 학교 급식",
    "tagline": "매점 음식보다 급식이 더 맛있어요",
    "description": (
        "전국 학교의 실제 급식 데이터(NEIS)를 받아와 오늘의 식단, 알레르기 달력, "
        "메뉴 투표, 급식 게임, 먹방 영상을 한곳에서 보여주는 학생용 웹 서비스입니다. "
        "오늘 급식을 보여줄 때 AI(Gemini)가 메뉴 중 가장 인기 있을 만한 메뉴를 골라 그 "
        "먹방 영상을 보여주고, 맛있게 먹는 팁과 건강 포인트도 함께 알려줍니다. 달력에서는 "
        "AI가 NEIS 공식 알레르기 코드에서 빠졌을 수 있는 성분을 참고용으로 보완해줍니다."
    ),
    "repositories": {
        "frontend": "schoolmeals-front (React 18 + Vite)",
        "backend": "schoolmeals-api (FastAPI + PostgreSQL)",
    },
}


# ── 1. 어떤 AI를 사용했는가 ─────────────────────────────────
# provider/model/fallback은 두 기능이 공유하고, 기능별 세부 사항은 capabilities에 나눠 담는다.
AI_USAGE = {
    "provider": "Google Gemini",
    "sdk": "google-genai (Google 공식 Gen AI SDK)",
    "defaultModel": settings.gemini_model,
    "modelSelectionReason": (
        "메뉴 하나 고르기, 짧은 팁 한 줄, 건강 포인트 몇 줄, 알레르기 성분 매칭처럼 모두 "
        "가벼운 작업이라 최상위 모델이 필요하지 않습니다. 토큰 단가가 가장 낮은 flash-lite "
        "계열을 선택해 비용을 최소화했고, 다른 모델로 바꿔야 할 이유가 없는 한 이 등급을 유지합니다."
    ),
    "fallback": (
        "GEMINI_API_KEY가 비어 있으면 StubAIProvider(더미 응답)로 자동 대체돼, "
        "키 없이도 전체 파이프라인이 동작합니다."
    ),
    "swappable": (
        "AIProvider 추상 클래스 뒤에 구현을 숨겨서 AI_PROVIDER 환경변수만 바꾸면 "
        "gemini / claude / stub 으로 교체됩니다. 오케스트레이션 코드는 그대로입니다."
    ),
    "capabilities": [
        {
            "name": "메뉴 분석 (/menu 페이지)",
            "purpose": "오늘 급식 메뉴 분석 — 인기 메뉴 선택 + 먹는 팁(있을 때만) + 건강 포인트",
            "structuredOutput": {
                "method": "response_mime_type='application/json' + Pydantic response_schema",
                "schema": {
                    "favorite": "str — 오늘 메뉴 중 학생들이 가장 좋아할 만한 메뉴 하나 (목록에 있는 표기 그대로)",
                    "eating_tip": "{dish, tip} | null — 더 맛있게 먹는 구체적인 방법. 억지로 만들지 않고, 없으면 null",
                    "health_notes": (
                        "list[{body_part, note}] — body_part는 프론트가 인체 그림 위 고정 좌표에 매핑할 수 있도록 "
                        "뇌/눈/목/심장/폐/위장/장/근육/뼈/혈액/피부/면역력/전신 13개 값으로만 제한(Literal)"
                    ),
                },
                "reason": (
                    "JSON 스키마를 강제해 파싱 실패 없이 그대로 프론트에 내려주고, body_part를 고정 목록으로 "
                    "제한해 프론트의 인체 그림이 항상 정해진 위치에 점을 찍을 수 있게 했습니다."
                ),
            },
            "callPolicy": {
                "when": "그 학교의 그 날짜 /menu 페이지에 최초 접속했을 때",
                "frequency": "학교(office_code+school_code) × 날짜(meal_date) 단위로 정확히 1회",
                "cache": "menu_insights 테이블 (office_code + school_code + meal_date 유니크)",
                "afterFirstCall": "이후 같은 학교·같은 날짜 방문자에게는 AI 호출 없이 DB에 저장된 값을 그대로 반환",
                "concurrency": (
                    "INSERT ... ON CONFLICT DO NOTHING 으로 status='pending' 행을 선점(claim)해, "
                    "여러 명이 동시에 같은 학교·같은 날짜에 들어와도 AI는 한 번만 호출됩니다. "
                    "선점하지 못한 요청은 done이 될 때까지 짧게 폴링합니다. (app/services/menu_insights.py)"
                ),
                "failure": "실패하면 status='failed'로 남기고 다음 요청이 재시도합니다.",
            },
            "costControls": [
                "학교×날짜당 정확히 1회만 호출하고 결과를 DB에 캐시",
                "인기 메뉴·먹는 팁·건강 포인트를 한 번의 호출로 묶어 캐시 미스일 때도 호출 1회만 발생",
                "출력 스키마를 고정해 재시도/재생성 낭비 방지",
                "먹는 팁은 억지로 만들지 않도록 프롬프트에서 명시 — null 허용으로 불필요한 텍스트 생성 방지",
            ],
        },
        {
            "name": "알레르기 보완 (/calendar 페이지)",
            "purpose": (
                "NEIS 공식 알레르기 코드(1~19)는 학교가 직접 태깅한 값이라 실제로 들어있어도 "
                "누락되는 경우가 있습니다 (예: '삼치된장박이구이'에 생선류 코드가 빠진 실제 사례로 "
                "확인됨). AI가 메뉴 이름만으로 확실히 알 수 있는 경우에 한해 빠진 성분을 참고용으로 "
                "보완합니다 — 공식 코드를 대체하지 않으며, 달력의 빨간색 경고 표시는 여전히 공식 "
                "코드만 사용하고 AI 추정치는 날짜 상세보기에 별도로만 표시됩니다."
            ),
            "structuredOutput": {
                "method": "response_mime_type='application/json' + Pydantic response_schema",
                "schema": {
                    "days": (
                        "list[{date, guesses: list[{dish, allergen, reason}]}] — allergen은 프론트와 "
                        "동일한 19개 NEIS 표준 알레르기 키로만 제한(Literal), 근거가 확실한 경우만 포함"
                    ),
                },
                "reason": (
                    "allergen을 프론트가 이미 쓰는 19개 키로 제한해 라벨 매핑이 항상 되게 하고, "
                    "'애매하면 절대 추측하지 마' 지시와 함께 서버에서도 이미 표시된 성분과 겹치는 "
                    "결과는 한 번 더 걸러내 안전(과잉 확신 방지) 쪽으로 설계했습니다."
                ),
            },
            "callPolicy": {
                "when": "그 학교의 달력 페이지에서, 화면에 보이는 날짜 중 아직 캐시되지 않았거나 메뉴가 바뀐 날짜가 있을 때",
                "frequency": "학교 × 날짜 단위로, 그 날짜의 메뉴 내용(해시)이 바뀌지 않는 한 정확히 1회",
                "cache": "meal_allergen_notes 테이블 (office_code + school_code + meal_date 유니크, menu_hash로 변경 감지)",
                "changeDetection": (
                    "달력에서 다른 달을 봤다가 돌아와도 메뉴가 그대로면 AI를 다시 부르지 않습니다. "
                    "메뉴 이름 목록의 해시(menu_hash)가 캐시된 값과 다를 때만 그 날짜를 재생성합니다."
                ),
                "batching": (
                    "달력 한 번에 여러 날짜(최대 한 달치)가 보이므로, 새로 생성이 필요한 날짜들을 "
                    "모아 한 번의 AI 호출로 함께 처리합니다 — 날짜마다 따로 호출하지 않습니다. "
                    "(app/services/allergen_notes.py)"
                ),
            },
            "costControls": [
                "학교×날짜당, 메뉴가 바뀌지 않는 한 정확히 1회만 호출하고 결과를 DB에 캐시",
                "한 달치 중 새로 생성이 필요한 날짜만 모아 한 번의 호출로 처리",
                "애매하면 답하지 않도록 프롬프트에서 명시 — 불필요한 항목 생성 방지",
            ],
        },
    ],
}


# ── 2. 어떤 프롬프트를 사용했는가 ───────────────────────────
_SAMPLE_DISHES = ["발아현미밥", "미역국", "제육볶음", "배추김치", "요구르트"]

PROMPTS = {
    "menuInsights": {
        "usedBy": "POST /meals/menu-insights (프론트 /menu 페이지, 학교×날짜당 최초 접속 시에만 호출 — 이후는 캐시)",
        "builder": "app/services/ai_provider.py 의 build_menu_insights_prompt()",
        "inputs": ["오늘 급식 메뉴 이름 목록 (프론트가 NEIS 급식식단정보를 파싱한 결과)"],
        "template": (
            "오늘 학교 급식 메뉴는 다음과 같아:\n{메뉴 목록}\n\n"
            "아래 세 가지를 알려줘.\n\n"
            "1) favorite: 학생들이 가장 좋아할 만한 메뉴 하나\n"
            "2) eatingTip: 더 맛있게 먹는 구체적인 방법 (괜찮은 게 없으면 절대 억지로 만들지 말고 null)\n"
            "3) healthNotes: 몸의 어느 부분에 어떻게 도움이 되는지 1~4개, 아주 간단하게"
        ),
        # 실제 코드가 만드는 프롬프트를 그대로 렌더링한 예시 (문서와 코드가 어긋나지 않게)
        "renderedExample": build_menu_insights_prompt(_SAMPLE_DISHES),
        "designNotes": [
            "'절대 억지로 만들지 말고 eatingTip을 null로 둬' — 없는 꿀조합을 지어내지 않도록 명시적으로 제한",
            "healthNotes의 body_part는 자유 텍스트가 아니라 고정된 13개 값(뇌/눈/목/심장/폐/위장/장/근육/뼈/혈액/피부/면역력/전신) "
            "중 하나로만 받도록 Pydantic Literal로 스키마를 제한 — 프론트의 인체 그림이 항상 정해진 좌표에 점을 찍을 수 있게 함",
            "밥·김치처럼 매일 나오는 기본 메뉴보다 메인 요리를 우선하도록 명시해 매일 비슷한 답만 나오지 않게 유도",
            "인기 메뉴·먹는 팁·건강 포인트 세 가지를 한 번의 호출로 묶어 방문당 AI 호출 횟수를 최소화",
        ],
    },
    "allergenNotes": {
        "usedBy": "POST /meals/allergen-notes (프론트 /calendar 페이지, 화면에 보이는 날짜 중 새로 생성이 필요한 날짜만)",
        "builder": "app/services/ai_provider.py 의 build_allergen_notes_prompt()",
        "inputs": [
            "날짜별 메뉴 이름 목록과, 그 메뉴에 이미 표시된 공식 알레르기 성분 (프론트가 NEIS 코드를 파싱한 결과)",
        ],
        "template": (
            "다음은 날짜별 학교 급식 메뉴와, 이미 공식적으로 표시된 알레르기 성분이야:\n"
            "{날짜별 메뉴 목록 + 이미 표시된 알레르기}\n\n"
            "메뉴 이름 자체에서 명확히 드러나는데 빠진 성분이 있으면 알려줘. 애매하면 절대 "
            "추측하지 마. 이미 표시된 성분은 다시 답하지 마. 목록 밖의 값은 쓰지 마."
        ),
        # 실제 코드가 만드는 프롬프트를 그대로 렌더링한 예시 (문서와 코드가 어긋나지 않게)
        "renderedExample": build_allergen_notes_prompt([
            {
                "date": "20260903",
                "dishes": [
                    {"name": "삼치된장박이구이", "knownAllergens": ["soy", "wheat", "sulfite"]},
                ],
            },
        ]),
        "designNotes": [
            "'조금이라도 애매하거나 확실하지 않으면 절대 추측하지 말고 답에 포함하지 마' — 알레르기는 "
            "안전과 직결되므로, 확신 없는 추정을 답하느니 아무 말도 안 하는 쪽을 강하게 우선함",
            "'이미 표시된 성분은 다시 답하지 마' — 그래도 모델이 가끔 어기므로, 서버(_serialize_allergen_notes)에서 "
            "그 메뉴 자신의 기존 표시와 겹치는 결과는 한 번 더 걸러냄 (실제로 이 문제를 겪고 추가한 방어 코드)",
            "allergen은 프론트가 이미 쓰는 19개 NEIS 표준 키로만 제한(Literal) — 라벨 매핑이 항상 되게 함",
            "결과는 어디까지나 참고용이며, 프론트의 공식 경고 표시(달력 빨간색)에는 이 결과를 쓰지 않고 "
            "날짜 상세보기에 '공식 정보 아님' 문구와 함께 별도로만 보여줌",
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
            "usage": "오늘 급식 메뉴 분석(/menu) + 알레르기 코드 보완(/calendar)",
            "quotaNote": (
                "둘 다 학교×날짜 단위로 DB에 캐시되어 메뉴/내용이 바뀌지 않는 한 재호출되지 "
                "않습니다. 여러 항목을 한 번의 호출로 묶고 가장 저렴한 flash-lite 계열을 써서 "
                "비용을 낮게 유지합니다."
            ),
        },
    },
    "tables": {
        "schools": "학교 기본정보 (사용자가 실제로 연 학교만 저장)",
        "meals": "급식 식단 원본 (NEIS row 그대로 보관)",
        "meal_ingest_states": "학교별로 어느 기간까지 가져왔는지 기록",
        "menu_insights": "학교×날짜별 AI 메뉴 분석 캐시 (pending/done/failed) — /menu 페이지가 실제로 쓰는 캐시",
        "meal_allergen_notes": "학교×날짜별 AI 알레르기 보완 캐시 (pending/done/failed, menu_hash로 변경 감지) — /calendar 페이지가 쓰는 캐시",
        "youtube_caches": "검색어별 유튜브 결과 캐시",
        "food_likes": "급식 메뉴 찜 수 (YYYY-MM 단위)",
        "menu_votes": "메뉴 투표 수 (ISO 주 단위)",
    },
    "externalCallPolicy": {
        "principle": "외부 호출은 '최초 1회 → DB 저장 → 이후 접속자는 DB 값' 원칙으로 통일",
        "rules": [
            {"대상": "AI 메뉴 분석", "호출 시점": "해당 학교의 해당 날짜 급식 페이지 최초 접속", "재호출": "없음 (학교×날짜당 1회, menu_insights 캐시)"},
            {"대상": "AI 알레르기 보완", "호출 시점": "달력에 보이는 날짜 중 캐시가 없거나 메뉴가 바뀐 날짜가 있을 때", "재호출": "없음 (학교×날짜당 1회, 메뉴 내용이 같으면 재호출 안 함)"},
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
        "name": "오늘의 식단 + AI 메뉴 분석",
        "route": "/menu",
        "description": (
            "선택한 학교의 오늘 급식(메뉴·알레르기·총 칼로리)을 보여주고, AI가 오늘 메뉴 중 "
            "가장 인기 있을 만한 메뉴를 골라 그 메뉴의 먹방 영상을 보여줍니다. 맛있게 먹는 팁이 "
            "있으면 함께 보여주고(억지로 만들지는 않음), 오늘 급식이 몸의 어디에 도움이 되는지 "
            "인체 그림 위에 간단히 표시합니다. 하단에는 그 학교의 이번 달 달력별 급식표도 있습니다."
        ),
        "dataSource": "NEIS 급식식단정보 + YouTube Data API v3 + Gemini(메뉴 분석, 학교×날짜당 1회 후 캐시)",
        "usesAI": True,
    },
    {
        "name": "알레르기 체크 달력",
        "route": "/calendar",
        "description": (
            "한 달치 급식을 달력으로 보여주고, 알레르기 항목을 고르면 해당 성분이 든 날을 "
            "빨간색으로 표시합니다 (NEIS 공식 코드 기준). 날짜를 누르면 메뉴·영양정보·원산지를 "
            "볼 수 있고, AI가 공식 코드에서 빠졌을 수 있는 성분을 찾아내면 '공식 정보 아님' "
            "문구와 함께 참고용으로 따로 보여줍니다."
        ),
        "dataSource": "NEIS 급식식단정보 (19종 알레르기 코드 파싱) + Gemini(알레르기 보완, 학교×날짜당 1회 후 캐시)",
        "usesAI": True,
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

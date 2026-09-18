"""
급식정보 라우터
─────────────────────────────────────────────────────────────
NEIS를 매 요청 프록시하지 않고, DB에 저장된 급식을 반환합니다.
아직 안 가져온 기간일 때만 ingest가 NEIS를 1회 호출해 채웁니다
(app/services/ingest.py 참고).

응답 형태는 NEIS 원본 구조를 그대로 유지합니다 — 프론트가
data.mealServiceDietInfo[1].row 를 파싱하고 있기 때문입니다.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services import allergen_notes, ingest, menu_insights, neis

logger = logging.getLogger("neis")

router = APIRouter()

_YMD = "%Y%m%d"


@router.get("/meals")
async def get_meals(
    atpt_ofcdc_sc_code: str = Query(..., description="시도교육청코드 (예: 서울=B10, 경기=J10)"),
    sd_schul_code: str = Query(..., description="표준학교코드"),
    mlsv_ymd: str | None = Query(None, description="급식일자 (YYYYMMDD)"),
    mlsv_from_ymd: str | None = Query(None, description="조회 시작일자 (YYYYMMDD)"),
    mlsv_to_ymd: str | None = Query(None, description="조회 종료일자 (YYYYMMDD)"),
    mmeal_sc_code: str | None = Query(None, description="식사코드 (1=조식, 2=중식, 3=석식)"),
    db: AsyncSession = Depends(get_db),
):
    from_ymd = mlsv_ymd or mlsv_from_ymd
    to_ymd = mlsv_ymd or mlsv_to_ymd
    if not from_ymd or not to_ymd:
        raise HTTPException(
            status_code=400,
            detail="mlsv_ymd 또는 mlsv_from_ymd/mlsv_to_ymd 를 지정해야 합니다.",
        )

    try:
        from_date = datetime.strptime(from_ymd, _YMD).date()
        to_date = datetime.strptime(to_ymd, _YMD).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜는 YYYYMMDD 형식이어야 합니다.")

    try:
        await ingest.ensure_meals(db, atpt_ofcdc_sc_code, sd_schul_code, from_ymd, to_ymd)
    except httpx.HTTPStatusError as e:
        logger.exception("NEIS API 호출 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 호출 실패: {e}")
    except httpx.RequestError as e:
        logger.exception("NEIS API 연결 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 연결 실패: {e}")

    rows = await ingest.fetch_stored_meals(
        db, atpt_ofcdc_sc_code, sd_schul_code, from_date, to_date, mmeal_sc_code
    )

    return {
        neis.MEAL_ENDPOINT: [
            {"head": [{"list_total_count": len(rows)}]},
            {"row": rows},
        ]
    }


class MenuInsightsRequest(BaseModel):
    atpt_ofcdc_sc_code: str
    sd_schul_code: str
    mlsv_ymd: str
    dishes: list[str]


@router.post("/meals/menu-insights")
async def get_menu_insights(body: MenuInsightsRequest, db: AsyncSession = Depends(get_db)):
    """그 학교의 그 날짜 급식 메뉴를 AI(Gemini)로 분석합니다 — 인기 메뉴, 맛있게 먹는 팁
    (있을 때만), 건강 포인트를 한 번의 호출로 함께 받습니다.

    학교(atpt_ofcdc_sc_code + sd_schul_code) × 날짜(mlsv_ymd) 단위로 캐시되어, 같은 학교의
    같은 날짜 급식 페이지에 처음 접속했을 때만 AI가 호출되고 이후 방문자에게는 DB에 저장된
    값을 그대로 반환합니다 (app/services/menu_insights.py — claim 패턴 기반 캐싱).
    dishes는 최초 생성 시에만 실제로 쓰이고, 캐시 히트 시에는 무시됩니다.
    """
    try:
        meal_date = datetime.strptime(body.mlsv_ymd, _YMD).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="mlsv_ymd는 YYYYMMDD 형식이어야 합니다.")

    dishes = [d.strip() for d in body.dishes if d.strip()]
    if not dishes:
        raise HTTPException(status_code=400, detail="dishes가 비어있습니다.")

    try:
        return await menu_insights.get_or_generate_menu_insights(
            db, body.atpt_ofcdc_sc_code, body.sd_schul_code, meal_date, dishes
        )
    except menu_insights.AIGenerationTimeout as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("AI 메뉴 분석 실패")
        raise HTTPException(status_code=502, detail=f"AI 메뉴 분석 실패: {e}")


class AllergenNotesDish(BaseModel):
    name: str
    knownAllergens: list[str] = []


class AllergenNotesDay(BaseModel):
    mlsv_ymd: str
    dishes: list[AllergenNotesDish]


class AllergenNotesRequest(BaseModel):
    atpt_ofcdc_sc_code: str
    sd_schul_code: str
    days: list[AllergenNotesDay]


@router.post("/meals/allergen-notes")
async def get_allergen_notes(body: AllergenNotesRequest, db: AsyncSession = Depends(get_db)):
    """그 학교의 달력에 보이는 날짜들에 대해, NEIS 공식 알레르기 코드에 빠졌을 수 있는
    성분을 AI(Gemini)가 메뉴 이름만으로 확실한 경우에 한해 보완합니다 (참고용 — 공식
    코드를 대체하지 않습니다).

    학교 × 날짜 단위로 DB에 캐시되어, 같은 학교의 같은 날짜는 메뉴 내용이 바뀌지 않는 한
    다시 AI를 호출하지 않습니다 — 달력에서 다른 달로 이동했다가 돌아와도 메뉴가 그대로면
    캐시된 값을 그대로 반환합니다 (app/services/allergen_notes.py). 요청에 포함된 날짜
    중 새로 생성이 필요한 날짜들은 한 번의 AI 호출로 함께 처리해 호출 횟수를 최소화합니다.
    """
    days = []
    for d in body.days:
        try:
            meal_date = datetime.strptime(d.mlsv_ymd, _YMD).date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"mlsv_ymd는 YYYYMMDD 형식이어야 합니다: {d.mlsv_ymd}")
        dishes = [{"name": dish.name.strip(), "knownAllergens": dish.knownAllergens} for dish in d.dishes if dish.name.strip()]
        if dishes:
            days.append({"meal_date": meal_date, "dishes": dishes})

    if not days:
        return {"notes": {}}

    try:
        notes = await allergen_notes.get_or_generate_allergen_notes(
            db, body.atpt_ofcdc_sc_code, body.sd_schul_code, days
        )
    except Exception as e:
        logger.exception("AI 알레르기 보완 실패")
        raise HTTPException(status_code=502, detail=f"AI 알레르기 보완 실패: {e}")

    return {"notes": notes}

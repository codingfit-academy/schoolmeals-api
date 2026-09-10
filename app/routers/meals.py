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
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services import ingest, neis

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

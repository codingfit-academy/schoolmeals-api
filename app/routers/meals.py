"""
급식정보 라우터
─────────────────────────────────────────────────────────────
NEIS API를 호출해 원본 응답을 그대로 반환합니다. (가공은 추후 작업)
"""
import logging

from fastapi import APIRouter, HTTPException, Query
import httpx

from ..services import neis

logger = logging.getLogger("neis")

router = APIRouter()


@router.get("/meals")
async def get_meals(
    atpt_ofcdc_sc_code: str = Query(..., description="시도교육청코드 (예: 서울=B10, 경기=J10)"),
    sd_schul_code: str = Query(..., description="표준학교코드"),
    mlsv_ymd: str | None = Query(None, description="급식일자 (YYYYMMDD)"),
    mlsv_from_ymd: str | None = Query(None, description="조회 시작일자 (YYYYMMDD)"),
    mlsv_to_ymd: str | None = Query(None, description="조회 종료일자 (YYYYMMDD)"),
):
    try:
        data = await neis.fetch_meal_info(
            atpt_ofcdc_sc_code=atpt_ofcdc_sc_code,
            sd_schul_code=sd_schul_code,
            mlsv_ymd=mlsv_ymd,
            mlsv_from_ymd=mlsv_from_ymd,
            mlsv_to_ymd=mlsv_to_ymd,
        )
    except httpx.HTTPStatusError as e:
        logger.exception("NEIS API 호출 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 호출 실패: {e}")
    except httpx.RequestError as e:
        logger.exception("NEIS API 연결 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 연결 실패: {e}")

    return data

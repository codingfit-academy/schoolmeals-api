"""
학교기본정보 라우터
─────────────────────────────────────────────────────────────
NEIS 학교기본정보 API를 호출해 프론트에서 바로 쓰기 좋은 형태로 간추려 반환합니다.
(지원 지역: 서울, 경기)
"""
import logging

from fastapi import APIRouter, HTTPException, Query
import httpx

from ..services import neis

logger = logging.getLogger("neis")

router = APIRouter()


@router.get("/schools")
async def get_schools(
    region: str = Query(..., description="지역명 (서울 | 경기)"),
):
    office_code = neis.REGION_OFFICE_CODES.get(region)
    if not office_code:
        supported = ", ".join(neis.REGION_OFFICE_CODES.keys())
        raise HTTPException(status_code=400, detail=f"지원하지 않는 지역입니다. (지원: {supported})")

    try:
        rows = await neis.fetch_all_schools(office_code)
    except httpx.HTTPStatusError as e:
        logger.exception("NEIS API 호출 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 호출 실패: {e}")
    except httpx.RequestError as e:
        logger.exception("NEIS API 연결 실패")
        raise HTTPException(status_code=502, detail=f"NEIS API 연결 실패: {e}")

    return [
        {
            "region": region,
            "officeCode": row.get("ATPT_OFCDC_SC_CODE"),
            "schoolCode": row.get("SD_SCHUL_CODE"),
            "name": row.get("SCHUL_NM"),
            "kind": row.get("SCHUL_KND_SC_NM"),
            "address": row.get("ORG_RDNMA"),
        }
        for row in rows
    ]

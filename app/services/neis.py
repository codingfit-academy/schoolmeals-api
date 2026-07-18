"""
NEIS(나이스 교육정보 개방포털) 급식식단정보 Open API 호출
─────────────────────────────────────────────────────────────
문서: https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17320190722180924242823
엔드포인트: GET {NEIS_BASE_URL}/mealServiceDietInfo

데이터 가공은 하지 않고, 호출 결과를 그대로 로그에 남기고 반환합니다.
"""
import logging

import httpx

from ..config import settings

logger = logging.getLogger("neis")

MEAL_ENDPOINT = "mealServiceDietInfo"


async def fetch_meal_info(
    atpt_ofcdc_sc_code: str,
    sd_schul_code: str,
    mlsv_ymd: str | None = None,
    mlsv_from_ymd: str | None = None,
    mlsv_to_ymd: str | None = None,
    p_index: int = 1,
    p_size: int = 100,
) -> dict:
    """NEIS 급식식단정보 API를 호출하고 원본 응답(dict)을 반환합니다."""
    params = {
        "KEY": settings.neis_api_key,
        "Type": "json",
        "pIndex": p_index,
        "pSize": p_size,
        "ATPT_OFCDC_SC_CODE": atpt_ofcdc_sc_code,
        "SD_SCHUL_CODE": sd_schul_code,
    }
    if mlsv_ymd:
        params["MLSV_YMD"] = mlsv_ymd
    if mlsv_from_ymd:
        params["MLSV_FROM_YMD"] = mlsv_from_ymd
    if mlsv_to_ymd:
        params["MLSV_TO_YMD"] = mlsv_to_ymd

    url = f"{settings.neis_base_url}/{MEAL_ENDPOINT}"

    # KEY는 로그에 남기지 않음
    logged_params = {k: v for k, v in params.items() if k != "KEY"}
    logger.info("NEIS API 요청: url=%s params=%s", url, logged_params)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)

    logger.info("NEIS API 응답: status=%s body=%s", response.status_code, response.text)

    response.raise_for_status()
    return response.json()

"""
NEIS(나이스 교육정보 개방포털) Open API 호출
─────────────────────────────────────────────────────────────
급식식단정보: https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17320190722180924242823
학교기본정보: https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17020190531110010107533

데이터 가공은 하지 않고, 호출 결과를 그대로 로그에 남기고 반환합니다.
(단, 학교 목록은 여러 페이지에 걸쳐 내려오므로 fetch_all_schools 에서 페이지를 모아 반환합니다.)
"""
import logging
import time

import httpx

from ..config import settings

logger = logging.getLogger("neis")

MEAL_ENDPOINT = "mealServiceDietInfo"
SCHOOL_ENDPOINT = "schoolInfo"

# 지역명 → 시도교육청코드
REGION_OFFICE_CODES = {
    "서울": "B10",
    "경기": "J10",
}

_SCHOOL_LIST_CACHE_TTL = 6 * 60 * 60  # 6시간 (학교 목록은 자주 바뀌지 않음)
_school_list_cache: dict[str, tuple[float, list[dict]]] = {}


async def fetch_meal_info(
    atpt_ofcdc_sc_code: str,
    sd_schul_code: str,
    mlsv_ymd: str | None = None,
    mlsv_from_ymd: str | None = None,
    mlsv_to_ymd: str | None = None,
    mmeal_sc_code: str | None = None,
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
    if mmeal_sc_code:
        params["MMEAL_SC_CODE"] = mmeal_sc_code

    return await _get(MEAL_ENDPOINT, params)


async def fetch_school_info(
    atpt_ofcdc_sc_code: str,
    schul_nm: str | None = None,
    p_index: int = 1,
    p_size: int = 1000,
) -> dict:
    """NEIS 학교기본정보 API를 호출하고 원본 응답(dict)을 반환합니다."""
    params = {
        "KEY": settings.neis_api_key,
        "Type": "json",
        "pIndex": p_index,
        "pSize": p_size,
        "ATPT_OFCDC_SC_CODE": atpt_ofcdc_sc_code,
    }
    if schul_nm:
        params["SCHUL_NM"] = schul_nm

    return await _get(SCHOOL_ENDPOINT, params)


async def fetch_all_schools(atpt_ofcdc_sc_code: str, max_pages: int = 10) -> list[dict]:
    """지정한 교육청코드의 학교 목록 전체를 페이지네이션하며 모아서 반환합니다.

    학교 목록은 지역당 최대 수천 건이라 자주 바뀌지 않으므로 메모리에 잠시 캐시합니다.
    """
    now = time.monotonic()
    cached = _school_list_cache.get(atpt_ofcdc_sc_code)
    if cached and now - cached[0] < _SCHOOL_LIST_CACHE_TTL:
        return cached[1]

    p_size = 1000
    schools: list[dict] = []
    for p_index in range(1, max_pages + 1):
        data = await fetch_school_info(atpt_ofcdc_sc_code, p_index=p_index, p_size=p_size)
        rows = extract_rows(data, SCHOOL_ENDPOINT)
        if not rows:
            break
        schools.extend(rows)
        if len(rows) < p_size:
            break

    _school_list_cache[atpt_ofcdc_sc_code] = (now, schools)
    return schools


def extract_rows(data: dict, key: str) -> list[dict]:
    """NEIS 응답 [{head:...}, {row:[...]}] 형태에서 row 리스트만 꺼냅니다.
    데이터가 없으면 head 없이 RESULT 만 오므로 그 경우 빈 리스트를 반환합니다."""
    section = data.get(key)
    if not section or len(section) < 2:
        return []
    return section[1].get("row", [])


async def _get(endpoint: str, params: dict) -> dict:
    url = f"{settings.neis_base_url}/{endpoint}"

    # KEY는 로그에 남기지 않음
    logged_params = {k: v for k, v in params.items() if k != "KEY"}
    logger.info("NEIS API 요청: url=%s params=%s", url, logged_params)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)

    logger.info("NEIS API 응답: status=%s body=%s", response.status_code, response.text)

    response.raise_for_status()
    return response.json()

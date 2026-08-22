"""
NEIS 배치 수집 — 학교기본정보 + 급식정보를 DB에 upsert
─────────────────────────────────────────────────────────────
평일 새벽 1시에 app/scheduler.py가 이 모듈의 run_daily_ingest()를 호출한다.
학교 단위로 실패를 격리한다 — 한 학교의 급식 조회가 실패해도 나머지 학교는
계속 처리한다.
"""
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.dialects.postgresql import insert

from ..database import SessionLocal
from ..models import Meal, School
from . import neis

logger = logging.getLogger("ingest")

MEAL_LOOKBACK_DAYS = 7
MEAL_LOOKAHEAD_DAYS = 14


async def run_daily_ingest() -> None:
    logger.info("배치 수집 시작")
    total_schools = 0
    total_meals = 0

    async with SessionLocal() as db:
        for region, office_code in neis.REGION_OFFICE_CODES.items():
            try:
                rows = await neis.fetch_all_schools(office_code)
            except (httpx.HTTPStatusError, httpx.RequestError):
                logger.exception("학교 목록 조회 실패: region=%s", region)
                continue

            school_codes = await _upsert_schools(db, region, office_code, rows)
            total_schools += len(school_codes)

            for school_code in school_codes:
                try:
                    total_meals += await _ingest_meals(db, office_code, school_code)
                except (httpx.HTTPStatusError, httpx.RequestError):
                    logger.exception(
                        "급식 조회 실패: office_code=%s school_code=%s", office_code, school_code
                    )
                    continue

    logger.info("배치 수집 완료: 학교 %d건, 급식(일자x끼니) %d건", total_schools, total_meals)


async def _upsert_schools(db, region: str, office_code: str, rows: list[dict]) -> list[str]:
    school_codes: list[str] = []
    for row in rows:
        school_code = row.get("SD_SCHUL_CODE")
        if not school_code:
            continue
        school_codes.append(school_code)

        values = {
            "region": region,
            "name": row.get("SCHUL_NM", ""),
            "kind": row.get("SCHUL_KND_SC_NM"),
            "address": row.get("ORG_RDNMA"),
        }
        stmt = (
            insert(School)
            .values(office_code=office_code, school_code=school_code, **values)
            .on_conflict_do_update(
                index_elements=["office_code", "school_code"],
                set_=values,
            )
        )
        await db.execute(stmt)

    await db.commit()
    return school_codes


async def _ingest_meals(db, office_code: str, school_code: str) -> int:
    today = date.today()
    from_ymd = (today - timedelta(days=MEAL_LOOKBACK_DAYS)).strftime("%Y%m%d")
    to_ymd = (today + timedelta(days=MEAL_LOOKAHEAD_DAYS)).strftime("%Y%m%d")

    data = await neis.fetch_meal_info(
        atpt_ofcdc_sc_code=office_code,
        sd_schul_code=school_code,
        mlsv_from_ymd=from_ymd,
        mlsv_to_ymd=to_ymd,
    )
    rows = neis.extract_rows(data, neis.MEAL_ENDPOINT)

    for row in rows:
        mlsv_ymd = row.get("MLSV_YMD")
        meal_type = row.get("MMEAL_SC_CODE")
        if not mlsv_ymd or not meal_type:
            continue

        values = {
            "menu_text": row.get("DDISH_NM"),
            "calorie_info": row.get("CAL_INFO"),
            "nutrition_info": row.get("NTR_INFO"),
            "origin_info": row.get("ORPLC_INFO"),
            "raw": row,
        }
        stmt = (
            insert(Meal)
            .values(
                office_code=office_code,
                school_code=school_code,
                meal_date=datetime.strptime(mlsv_ymd, "%Y%m%d").date(),
                meal_type=meal_type,
                **values,
            )
            .on_conflict_do_update(
                index_elements=["office_code", "school_code", "meal_date", "meal_type"],
                set_=values,
            )
        )
        await db.execute(stmt)

    await db.commit()
    return len(rows)

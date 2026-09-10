"""
NEIS 지연(lazy) 수집 — 필요할 때만 가져와서 DB에 저장
─────────────────────────────────────────────────────────────
전 지역/전 학교를 매일 새벽에 통째로 긁어오던 배치를 없애고, 사용자가 실제로
연 학교에 대해 "아직 안 가져온 기간"만 NEIS를 호출하도록 바꿨다.

  ensure_school : schools 행이 없을 때만 학교 1건 조회 → upsert
  ensure_meals  : 요청 구간이 이미 가져온 구간(meal_ingest_states) 안이면
                  NEIS 호출도 DB 쓰기도 하지 않고 즉시 반환

즉 NEIS 호출과 DB 쓰기는 "처음 보는 학교" 또는 "처음 보는 기간"에서만 발생한다.
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meal, MealIngestState, School
from . import neis

logger = logging.getLogger("ingest")

# 한 번 가져올 때 요청 구간 앞뒤로 덧붙이는 여유분 —
# 사용자가 달력을 한 달씩 넘길 때 매번 NEIS를 때리지 않도록 한다.
_PADDING_DAYS = 14

_YMD = "%Y%m%d"


async def ensure_school(db: AsyncSession, office_code: str, school_code: str) -> School | None:
    """학교 행이 없을 때만 NEIS에서 해당 학교 1건을 가져와 저장한다."""
    school = await _fetch_school_row(db, office_code, school_code)
    if school is not None:
        return school

    region = _region_of(office_code)
    if region is None:
        return None

    rows = await neis.fetch_all_schools(office_code)
    row = next((r for r in rows if r.get("SD_SCHUL_CODE") == school_code), None)
    if row is None:
        logger.warning("NEIS에 없는 학교: %s/%s", office_code, school_code)
        return None

    values = {
        "region": region,
        "name": row.get("SCHUL_NM", ""),
        "kind": row.get("SCHUL_KND_SC_NM"),
        "address": row.get("ORG_RDNMA"),
    }
    await db.execute(
        insert(School)
        .values(office_code=office_code, school_code=school_code, **values)
        .on_conflict_do_update(index_elements=["office_code", "school_code"], set_=values)
    )
    await db.commit()
    logger.info("학교 정보 최초 저장: %s/%s %s", office_code, school_code, values["name"])
    return await _fetch_school_row(db, office_code, school_code)


async def ensure_meals(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    from_ymd: str,
    to_ymd: str,
) -> None:
    """요청 구간이 아직 안 가져온 범위를 포함할 때만 NEIS를 호출해 채운다."""
    want_from = datetime.strptime(from_ymd, _YMD).date()
    want_to = datetime.strptime(to_ymd, _YMD).date()

    state = await _fetch_ingest_state(db, office_code, school_code)
    if state is not None and state.covered_from <= want_from and want_to <= state.covered_to:
        return

    # 기존 구간과 요청 구간을 합친 뒤 여유분을 붙여 한 번에 가져온다
    fetch_from = want_from - timedelta(days=_PADDING_DAYS)
    fetch_to = want_to + timedelta(days=_PADDING_DAYS)
    if state is not None:
        fetch_from = min(fetch_from, state.covered_from)
        fetch_to = max(fetch_to, state.covered_to)

    await ensure_school(db, office_code, school_code)

    data = await neis.fetch_meal_info(
        atpt_ofcdc_sc_code=office_code,
        sd_schul_code=school_code,
        mlsv_from_ymd=fetch_from.strftime(_YMD),
        mlsv_to_ymd=fetch_to.strftime(_YMD),
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
        await db.execute(
            insert(Meal)
            .values(
                office_code=office_code,
                school_code=school_code,
                meal_date=datetime.strptime(mlsv_ymd, _YMD).date(),
                meal_type=meal_type,
                **values,
            )
            .on_conflict_do_update(
                index_elements=["office_code", "school_code", "meal_date", "meal_type"],
                set_=values,
            )
        )

    covered = {"covered_from": fetch_from, "covered_to": fetch_to}
    await db.execute(
        insert(MealIngestState)
        .values(office_code=office_code, school_code=school_code, **covered)
        .on_conflict_do_update(index_elements=["office_code", "school_code"], set_=covered)
    )
    await db.commit()
    logger.info(
        "급식 수집: %s/%s %s~%s (%d건)",
        office_code, school_code, fetch_from, fetch_to, len(rows),
    )


async def fetch_stored_meals(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    from_date: date,
    to_date: date,
    meal_type: str | None = None,
) -> list[dict]:
    """DB에 저장해둔 NEIS 원본 row들을 날짜순으로 반환한다."""
    stmt = (
        select(Meal)
        .where(
            Meal.office_code == office_code,
            Meal.school_code == school_code,
            Meal.meal_date >= from_date,
            Meal.meal_date <= to_date,
        )
        .order_by(Meal.meal_date, Meal.meal_type)
    )
    if meal_type:
        stmt = stmt.where(Meal.meal_type == meal_type)

    result = await db.execute(stmt)
    return [m.raw for m in result.scalars().all() if m.raw]


async def _fetch_school_row(db: AsyncSession, office_code: str, school_code: str) -> School | None:
    result = await db.execute(
        select(School).where(School.office_code == office_code, School.school_code == school_code)
    )
    return result.scalar_one_or_none()


async def _fetch_ingest_state(
    db: AsyncSession, office_code: str, school_code: str
) -> MealIngestState | None:
    result = await db.execute(
        select(MealIngestState).where(
            MealIngestState.office_code == office_code,
            MealIngestState.school_code == school_code,
        )
    )
    return result.scalar_one_or_none()


def _region_of(office_code: str) -> str | None:
    for region, code in neis.REGION_OFFICE_CODES.items():
        if code == office_code:
            return region
    return None

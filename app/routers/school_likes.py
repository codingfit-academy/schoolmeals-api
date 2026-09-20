"""
학교별 급식 '좋아요' 라우터
─────────────────────────────────────────────────────────────
랜딩페이지 '이번 달 가장 인기있는 급식 학교' 순위의 실제 근거입니다.
학교(office_code + school_code) × 날짜(meal_date) 단위로 좋아요 수를 세고,
이번 달 총합이 높은 학교 순으로 보여줍니다. 각 학교 카드에는 그 학교가 이번 달
가장 많이 좋아요를 받은 날짜의 실제 급식(menuText/calorieInfo)을 함께 내려줘서,
프론트가 기존 parseDishes/parseKcal로 그대로 렌더링할 수 있게 합니다.

중복 클릭 방지는 서버가 아니라 프론트(localStorage, 학교×날짜 단위)가 담당합니다 —
이 앱에는 로그인이 없어서 "누가 눌렀는지"를 서버가 구분할 방법이 없기 때문입니다.
"""
import calendar
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Meal, School, SchoolMealLike

router = APIRouter()

_YMD = "%Y%m%d"


def _current_year_month() -> str:
    return date.today().strftime("%Y-%m")


@router.post("/schools/{office_code}/{school_code}/meals/{meal_date}/like")
async def like_school_meal(
    office_code: str = Path(..., max_length=10),
    school_code: str = Path(..., max_length=10),
    meal_date: str = Path(..., description="급식일자 (YYYYMMDD)"),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed_date = datetime.strptime(meal_date, _YMD).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="meal_date는 YYYYMMDD 형식이어야 합니다.")

    stmt = (
        insert(SchoolMealLike)
        .values(office_code=office_code, school_code=school_code, meal_date=parsed_date, count=1)
        .on_conflict_do_update(
            index_elements=["office_code", "school_code", "meal_date"],
            set_={"count": SchoolMealLike.__table__.c.count + 1},
        )
        .returning(SchoolMealLike.count)
    )
    result = await db.execute(stmt)
    count = result.scalar_one()
    await db.commit()
    return {"officeCode": office_code, "schoolCode": school_code, "mealDate": meal_date, "count": count}


@router.get("/schools/top-liked")
async def get_top_liked_schools(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (기본: 이번 달)"),
    limit: int = Query(6, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    year_month = month or _current_year_month()
    year, mon = (int(p) for p in year_month.split("-"))
    month_start = date(year, mon, 1)
    month_end = date(year, mon, calendar.monthrange(year, mon)[1])

    totals_stmt = (
        select(
            SchoolMealLike.office_code,
            SchoolMealLike.school_code,
            func.sum(SchoolMealLike.count).label("total"),
        )
        .where(SchoolMealLike.meal_date >= month_start, SchoolMealLike.meal_date <= month_end)
        .group_by(SchoolMealLike.office_code, SchoolMealLike.school_code)
        .order_by(func.sum(SchoolMealLike.count).desc())
        .limit(limit)
    )
    totals = (await db.execute(totals_stmt)).all()

    schools = []
    for office_code, school_code, total in totals:
        top_date_stmt = (
            select(SchoolMealLike.meal_date)
            .where(
                SchoolMealLike.office_code == office_code,
                SchoolMealLike.school_code == school_code,
                SchoolMealLike.meal_date >= month_start,
                SchoolMealLike.meal_date <= month_end,
            )
            .order_by(SchoolMealLike.count.desc())
            .limit(1)
        )
        top_date = (await db.execute(top_date_stmt)).scalar_one()

        school_row = (
            await db.execute(
                select(School).where(School.office_code == office_code, School.school_code == school_code)
            )
        ).scalar_one_or_none()

        meal_row = (
            await db.execute(
                select(Meal).where(
                    Meal.office_code == office_code,
                    Meal.school_code == school_code,
                    Meal.meal_date == top_date,
                    Meal.meal_type == "2",
                )
            )
        ).scalar_one_or_none()

        schools.append({
            "officeCode": office_code,
            "schoolCode": school_code,
            "schoolName": school_row.name if school_row else school_code,
            "totalLikes": int(total),
            "topDate": top_date.strftime(_YMD),
            "menuText": meal_row.menu_text if meal_row else None,
            "calorieInfo": meal_row.calorie_info if meal_row else None,
        })

    return {"month": year_month, "schools": schools}

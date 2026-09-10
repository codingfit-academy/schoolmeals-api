"""
급식 메뉴 '찜' 라우터
─────────────────────────────────────────────────────────────
랜딩페이지 '이번 달 BEST 급식' 순위의 실제 근거가 되는 카운터입니다.
문구가 '이번 달'이므로 YYYY-MM 단위로 집계합니다.
"""
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import FoodLike

router = APIRouter()


def current_year_month() -> str:
    return date.today().strftime("%Y-%m")


@router.get("/foods")
async def list_food_likes(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (기본: 이번 달)"),
    db: AsyncSession = Depends(get_db),
):
    year_month = month or current_year_month()
    result = await db.execute(
        select(FoodLike)
        .where(FoodLike.year_month == year_month)
        .order_by(FoodLike.count.desc())
    )
    return [
        {"slug": row.slug, "count": row.count, "yearMonth": row.year_month}
        for row in result.scalars().all()
    ]


@router.post("/foods/{slug}/like")
async def like_food(
    slug: str = Path(..., max_length=50),
    db: AsyncSession = Depends(get_db),
):
    year_month = current_year_month()
    stmt = (
        insert(FoodLike)
        .values(slug=slug, year_month=year_month, count=1)
        .on_conflict_do_update(
            index_elements=["slug", "year_month"],
            set_={"count": FoodLike.__table__.c.count + 1},
        )
        .returning(FoodLike.count)
    )
    result = await db.execute(stmt)
    count = result.scalar_one()
    await db.commit()
    return {"slug": slug, "count": count, "yearMonth": year_month}

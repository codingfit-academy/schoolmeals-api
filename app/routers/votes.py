"""
메뉴 투표 라우터
─────────────────────────────────────────────────────────────
투표 페이지가 '이번 주' 기준이라 ISO 주(2026-W37) 단위로 집계합니다.
주가 바뀌면 자동으로 새 집계가 시작됩니다.
"""
from datetime import date

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import MenuVote

router = APIRouter()


def current_iso_week() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


@router.get("/votes")
async def get_votes(db: AsyncSession = Depends(get_db)):
    iso_week = current_iso_week()
    result = await db.execute(select(MenuVote).where(MenuVote.iso_week == iso_week))
    counts = {row.option_key: row.count for row in result.scalars().all()}
    return {"week": iso_week, "counts": counts}


@router.post("/votes/{option_key}")
async def cast_vote(
    option_key: str = Path(..., max_length=20),
    db: AsyncSession = Depends(get_db),
):
    iso_week = current_iso_week()
    stmt = (
        insert(MenuVote)
        .values(option_key=option_key, iso_week=iso_week, count=1)
        .on_conflict_do_update(
            index_elements=["option_key", "iso_week"],
            set_={"count": MenuVote.__table__.c.count + 1},
        )
        .returning(MenuVote.count)
    )
    result = await db.execute(stmt)
    count = result.scalar_one()
    await db.commit()
    return {"week": iso_week, "optionKey": option_key, "count": count}

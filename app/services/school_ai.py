"""
학교별 AI 소개 캐싱 오케스트레이션
─────────────────────────────────────────────────────────────
학교당 AI는 정확히 한 번만 호출된다. school_ai_contents 테이블의
UniqueConstraint(office_code, school_code) + status를 이용한 "claim" 패턴으로
동시 요청에도 중복 AI 호출이 일어나지 않게 한다.

흐름:
  1) INSERT ... ON CONFLICT DO NOTHING 으로 status='pending' 행 선점 시도
  2) 선점 성공 → 이 요청이 생성 담당 → AI 호출 → status='done'으로 UPDATE
  3) 선점 실패(이미 행 존재) → 기존 행 조회
     - done    → 캐시된 content 그대로 반환 (AI 재호출 없음 — 핵심 요구사항)
     - pending → 다른 요청이 지금 생성 중 → 짧게 폴링하다 done 되면 반환
     - failed  → 다시 선점 시도(재시도 허용)
"""
import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meal, School, SchoolAiContent
from .ai_provider import get_ai_provider

logger = logging.getLogger("school_ai")

_POLL_INTERVAL = 0.3
_POLL_TIMEOUT = 10.0
_RECENT_MEALS_LIMIT = 10

MEAL_TYPE_NAMES = {"1": "조식", "2": "중식", "3": "석식"}


class SchoolNotFound(Exception):
    """DB에 아직 없는 학교 (배치 미수집 또는 잘못된 코드)"""


class AIGenerationTimeout(Exception):
    """다른 요청이 진행 중인 AI 생성이 타임아웃 내에 끝나지 않았을 때"""


async def get_or_generate_school_intro(
    db: AsyncSession, office_code: str, school_code: str
) -> dict:
    school = await _fetch_school_dict(db, office_code, school_code)
    if school is None:
        raise SchoolNotFound(f"학교를 찾을 수 없습니다: {office_code}/{school_code}")

    if await _try_claim(db, office_code, school_code):
        return await _generate_and_save(db, office_code, school_code, school)

    row = await _fetch_row(db, office_code, school_code)
    if row.status == "done":
        return _serialize(row)

    if row.status == "failed" and await _try_reclaim_failed(db, office_code, school_code):
        return await _generate_and_save(db, office_code, school_code, school)

    return await _wait_for_done(db, office_code, school_code)


async def _try_claim(db: AsyncSession, office_code: str, school_code: str) -> bool:
    stmt = (
        insert(SchoolAiContent)
        .values(office_code=office_code, school_code=school_code, status="pending")
        .on_conflict_do_nothing(index_elements=["office_code", "school_code"])
        .returning(SchoolAiContent.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.first() is not None


async def _try_reclaim_failed(db: AsyncSession, office_code: str, school_code: str) -> bool:
    stmt = (
        update(SchoolAiContent)
        .where(
            SchoolAiContent.office_code == office_code,
            SchoolAiContent.school_code == school_code,
            SchoolAiContent.status == "failed",
        )
        .values(status="pending")
        .returning(SchoolAiContent.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.first() is not None


async def _fetch_row(db: AsyncSession, office_code: str, school_code: str) -> SchoolAiContent:
    stmt = select(SchoolAiContent).where(
        SchoolAiContent.office_code == office_code,
        SchoolAiContent.school_code == school_code,
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def _wait_for_done(db: AsyncSession, office_code: str, school_code: str) -> dict:
    elapsed = 0.0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        db.expire_all()
        row = await _fetch_row(db, office_code, school_code)
        if row.status == "done":
            return _serialize(row)
        if row.status == "failed":
            break
    raise AIGenerationTimeout(f"{office_code}/{school_code} AI 생성 대기 타임아웃")


async def _generate_and_save(
    db: AsyncSession, office_code: str, school_code: str, school: dict
) -> dict:
    meals = await _fetch_recent_meals(db, office_code, school_code)
    provider = get_ai_provider()

    try:
        content = await provider.generate_school_intro(school, meals)
    except Exception:
        logger.exception("AI 학교소개 생성 실패: %s/%s", office_code, school_code)
        await db.execute(
            update(SchoolAiContent)
            .where(
                SchoolAiContent.office_code == office_code,
                SchoolAiContent.school_code == school_code,
            )
            .values(status="failed")
        )
        await db.commit()
        raise

    await db.execute(
        update(SchoolAiContent)
        .where(
            SchoolAiContent.office_code == office_code,
            SchoolAiContent.school_code == school_code,
        )
        .values(status="done", content=content, model=provider.name, generated_at=func.now())
    )
    await db.commit()

    row = await _fetch_row(db, office_code, school_code)
    return _serialize(row)


async def _fetch_school_dict(
    db: AsyncSession, office_code: str, school_code: str
) -> dict | None:
    stmt = select(School).where(
        School.office_code == office_code, School.school_code == school_code
    )
    result = await db.execute(stmt)
    school = result.scalar_one_or_none()
    if school is None:
        return None
    return {"name": school.name, "kind": school.kind, "address": school.address}


async def _fetch_recent_meals(
    db: AsyncSession, office_code: str, school_code: str
) -> list[dict]:
    stmt = (
        select(Meal)
        .where(Meal.office_code == office_code, Meal.school_code == school_code)
        .order_by(Meal.meal_date.desc())
        .limit(_RECENT_MEALS_LIMIT)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "meal_date": m.meal_date.isoformat(),
            "meal_type_name": MEAL_TYPE_NAMES.get(m.meal_type, m.meal_type),
            "menu_text": m.menu_text,
        }
        for m in rows
    ]


def _serialize(row: SchoolAiContent) -> dict:
    return {
        "status": row.status,
        "content": row.content,
        "model": row.model,
        "generatedAt": row.generated_at.isoformat() if row.generated_at else None,
    }

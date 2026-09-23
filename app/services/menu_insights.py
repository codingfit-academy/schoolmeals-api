"""
학교 × 날짜별 AI 메뉴 분석 캐싱 오케스트레이션
─────────────────────────────────────────────────────────────
그 학교의 그 날짜 급식 페이지에 AI는 정확히 한 번만 호출된다.
menu_insights 테이블의 UniqueConstraint(office_code, school_code, meal_date) +
status를 이용한 "claim" 패턴으로 동시 요청에도 중복 AI 호출이 일어나지 않게 한다.

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
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MenuInsight
from .ai_provider import get_ai_provider

logger = logging.getLogger("menu_insights")

_POLL_INTERVAL = 0.3
_POLL_TIMEOUT = 10.0


class AIGenerationTimeout(Exception):
    """다른 요청이 진행 중인 AI 생성이 타임아웃 내에 끝나지 않았을 때"""


async def get_or_generate_menu_insights(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    meal_date: date,
    dishes: list[str],
) -> dict:
    if await _try_claim(db, office_code, school_code, meal_date):
        return await _generate_and_save(db, office_code, school_code, meal_date, dishes)

    row = await _fetch_row(db, office_code, school_code, meal_date)
    if row.status == "done" and not _is_outdated(row.content):
        return _serialize(row)

    # 여기부터는 재생성 대상이다:
    #  - done인데 예전 프롬프트로 저장돼 새 항목(영양 밸런스·검색어)이 비어 있거나
    #  - 지난번 생성이 failed로 끝난 경우
    if row.status != "pending" and await _try_reclaim(db, office_code, school_code, meal_date):
        try:
            return await _generate_and_save(db, office_code, school_code, meal_date, dishes)
        except Exception:
            # 이미 저장해 둔 값이 있으면 에러 대신 그거라도 보여준다 —
            # 새 항목을 못 채웠다고 예전부터 잘 보이던 팁·건강 포인트까지 사라지면 안 된다.
            if row.content:
                logger.warning(
                    "재생성 실패 — 저장돼 있던 값으로 응답합니다: %s/%s/%s",
                    office_code, school_code, meal_date,
                )
                return _serialize(row)
            raise

    return await _wait_for_done(db, office_code, school_code, meal_date)


async def _try_claim(db: AsyncSession, office_code: str, school_code: str, meal_date: date) -> bool:
    stmt = (
        insert(MenuInsight)
        .values(office_code=office_code, school_code=school_code, meal_date=meal_date, status="pending")
        .on_conflict_do_nothing(index_elements=["office_code", "school_code", "meal_date"])
        .returning(MenuInsight.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.first() is not None


# content에 이 키들이 모두 있어야 최신 스키마로 저장된 캐시다 (프롬프트에 항목이 추가될 때마다 갱신).
_REQUIRED_CONTENT_KEYS = ("balance", "searchKeywords")


def _is_outdated(content: dict | None) -> bool:
    """예전 프롬프트로 저장된 캐시인지 판단한다 — DB 마이그레이션 없이 내용만으로 구분한다."""
    return not content or any(key not in content for key in _REQUIRED_CONTENT_KEYS)


async def _try_reclaim(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date
) -> bool:
    """재생성 대상(done인데 예전 스키마이거나, failed인 행)을 pending으로 되돌려 선점한다.
    status != 'pending' 조건이 있어서 동시 요청 중 한 쪽만 성공한다."""
    stmt = (
        update(MenuInsight)
        .where(
            MenuInsight.office_code == office_code,
            MenuInsight.school_code == school_code,
            MenuInsight.meal_date == meal_date,
            MenuInsight.status != "pending",
        )
        .values(status="pending")
        .returning(MenuInsight.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.first() is not None


async def _fetch_row(db: AsyncSession, office_code: str, school_code: str, meal_date: date) -> MenuInsight:
    stmt = select(MenuInsight).where(
        MenuInsight.office_code == office_code,
        MenuInsight.school_code == school_code,
        MenuInsight.meal_date == meal_date,
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def _wait_for_done(db: AsyncSession, office_code: str, school_code: str, meal_date: date) -> dict:
    elapsed = 0.0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        db.expire_all()
        row = await _fetch_row(db, office_code, school_code, meal_date)
        if row.status == "done":
            return _serialize(row)
        if row.status == "failed":
            break
    raise AIGenerationTimeout(f"{office_code}/{school_code}/{meal_date} 메뉴 분석 대기 타임아웃")


async def _generate_and_save(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date, dishes: list[str]
) -> dict:
    provider = get_ai_provider()

    try:
        content = await provider.analyze_menu(dishes)
    except Exception:
        logger.exception("AI 메뉴 분석 실패: %s/%s/%s", office_code, school_code, meal_date)
        await db.execute(
            update(MenuInsight)
            .where(
                MenuInsight.office_code == office_code,
                MenuInsight.school_code == school_code,
                MenuInsight.meal_date == meal_date,
            )
            .values(status="failed")
        )
        await db.commit()
        raise

    await db.execute(
        update(MenuInsight)
        .where(
            MenuInsight.office_code == office_code,
            MenuInsight.school_code == school_code,
            MenuInsight.meal_date == meal_date,
        )
        .values(status="done", content=content, model=provider.name, generated_at=func.now())
    )
    await db.commit()

    row = await _fetch_row(db, office_code, school_code, meal_date)
    return _serialize(row)


def _serialize(row: MenuInsight) -> dict:
    content = row.content or {}
    return {
        "eatingTip": content.get("eatingTip"),
        "healthNotes": content.get("healthNotes", []),
        "balance": content.get("balance"),
        "searchKeywords": content.get("searchKeywords", []),
        "model": row.model,
        "generatedAt": row.generated_at.isoformat() if row.generated_at else None,
    }

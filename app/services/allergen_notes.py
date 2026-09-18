"""
학교 × 날짜별 AI 알레르기 보완 캐싱 오케스트레이션 (/calendar 페이지)
─────────────────────────────────────────────────────────────
menu_insights.py와 같은 claim 패턴을 쓰되, 한 가지가 다르다: 달력은 한 번에
여러 날짜(한 달치)를 보여주므로, 날짜별로 독립적으로 캐시하고 "그 날짜의
메뉴 내용이 실제로 바뀌었는지(menu_hash)"까지 함께 확인한다. 메뉴가 그대로면
달력을 다시 봐도(다른 달 갔다가 돌아와도) AI를 재호출하지 않는다.

흐름 (날짜 하나당):
  1) 그 날짜 행이 없으면 INSERT ... ON CONFLICT DO NOTHING 으로 선점
  2) 이미 있는데 status가 pending이 아니고(done/failed) menu_hash가 달라졌거나
     status가 failed면 재선점(UPDATE status='pending', menu_hash=새 값)
  3) 선점 성공한 날짜들만 모아 AI를 "한 번의 호출"로 함께 요청 (호출 횟수 최소화)
  4) 선점 못한 날짜는 있는 그대로(done인 것만) 반환 — pending인 건 이번 응답에서는
     보완 정보 없이 넘어간다 (다음 새로고침 때 다시 시도됨; 학교 단위 트래픽에서
     동시 충돌 가능성은 낮으므로 폴링 없이 단순하게 처리)
"""
import hashlib
import logging
from datetime import date

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MealAllergenNote
from .ai_provider import get_ai_provider

logger = logging.getLogger("allergen_notes")


def _hash_dishes(dish_names: list[str]) -> str:
    return hashlib.sha256("|".join(dish_names).encode("utf-8")).hexdigest()[:32]


async def get_or_generate_allergen_notes(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    days: list[dict],
) -> dict[str, list[dict]]:
    """days: [{"meal_date": date, "dishes": [{"name": str, "knownAllergens": list[str]}]}]
    반환: {"YYYY-MM-DD": [{"dish": str, "allergen": str, "reason": str}]}
    """
    results: dict[str, list[dict]] = {}
    to_generate: list[dict] = []  # {meal_date, ymd, menu_hash, dishes}

    for d in days:
        dish_names = [dish["name"] for dish in d["dishes"]]
        if not dish_names:
            continue
        menu_hash = _hash_dishes(dish_names)
        ymd = d["meal_date"].isoformat()

        if await _try_claim_or_reclaim(db, office_code, school_code, d["meal_date"], menu_hash):
            to_generate.append({**d, "ymd": ymd, "menu_hash": menu_hash})
            continue

        row = await _fetch_row(db, office_code, school_code, d["meal_date"])
        if row is not None and row.status == "done" and row.menu_hash == menu_hash:
            results[ymd] = row.content or []
        # pending(다른 요청이 생성 중)이거나 방금 막 바뀐 해시로 아직 선점 안 된 경우는
        # 이번 응답에서 생략 — 다음 요청 때 다시 시도된다.

    if to_generate:
        provider = get_ai_provider()
        try:
            generated = await provider.suggest_allergens([
                {
                    "date": g["meal_date"].strftime("%Y%m%d"),
                    "dishes": [
                        {"name": dish["name"], "knownAllergens": dish["knownAllergens"]}
                        for dish in g["dishes"]
                    ],
                }
                for g in to_generate
            ])
        except Exception:
            logger.exception("AI 알레르기 보완 실패: %s/%s (%d일)", office_code, school_code, len(to_generate))
            for g in to_generate:
                await _mark_failed(db, office_code, school_code, g["meal_date"])
        else:
            for g in to_generate:
                ymd_compact = g["meal_date"].strftime("%Y%m%d")
                guesses = generated.get(ymd_compact, [])
                await _save_done(db, office_code, school_code, g["meal_date"], g["menu_hash"], guesses, provider.name)
                results[g["ymd"]] = guesses

    return results


async def _try_claim_or_reclaim(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date, menu_hash: str
) -> bool:
    stmt = (
        insert(MealAllergenNote)
        .values(
            office_code=office_code, school_code=school_code, meal_date=meal_date,
            menu_hash=menu_hash, status="pending",
        )
        .on_conflict_do_nothing(index_elements=["office_code", "school_code", "meal_date"])
        .returning(MealAllergenNote.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    if result.first() is not None:
        return True

    # 이미 있는 행 — 메뉴가 바뀌었거나(해시 다름) 이전에 실패했으면 재선점.
    # status='pending'인 행은 다른 요청이 지금 생성 중일 수 있으니 건드리지 않는다.
    stmt2 = (
        update(MealAllergenNote)
        .where(
            MealAllergenNote.office_code == office_code,
            MealAllergenNote.school_code == school_code,
            MealAllergenNote.meal_date == meal_date,
            MealAllergenNote.status != "pending",
            or_(MealAllergenNote.menu_hash != menu_hash, MealAllergenNote.status == "failed"),
        )
        .values(status="pending", menu_hash=menu_hash)
        .returning(MealAllergenNote.id)
    )
    result2 = await db.execute(stmt2)
    await db.commit()
    return result2.first() is not None


async def _fetch_row(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date
) -> MealAllergenNote | None:
    stmt = select(MealAllergenNote).where(
        MealAllergenNote.office_code == office_code,
        MealAllergenNote.school_code == school_code,
        MealAllergenNote.meal_date == meal_date,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _mark_failed(db: AsyncSession, office_code: str, school_code: str, meal_date: date) -> None:
    await db.execute(
        update(MealAllergenNote)
        .where(
            MealAllergenNote.office_code == office_code,
            MealAllergenNote.school_code == school_code,
            MealAllergenNote.meal_date == meal_date,
        )
        .values(status="failed")
    )
    await db.commit()


async def _save_done(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    meal_date: date,
    menu_hash: str,
    guesses: list[dict],
    model_name: str,
) -> None:
    from sqlalchemy import func

    await db.execute(
        update(MealAllergenNote)
        .where(
            MealAllergenNote.office_code == office_code,
            MealAllergenNote.school_code == school_code,
            MealAllergenNote.meal_date == meal_date,
        )
        .values(status="done", content=guesses, model=model_name, menu_hash=menu_hash, generated_at=func.now())
    )
    await db.commit()

"""
학교 × 날짜별 '유튜버들이 가장 추천하는 식사법' 캐싱 오케스트레이션 (/menu 페이지)
─────────────────────────────────────────────────────────────
그 날 메뉴로 찾은 유튜브 먹방 영상들의 제목·채널·설명을 AI가 읽고, 사람들이 실제로
어떻게 먹는지 정리한다. 여러 영상에서 반복되는 방법일수록 많이 먹는 방법이므로
'가장 추천하는 식사법'으로 앞에 세운다.

AI는 그 학교의 그 날짜에 처음 들어왔을 때 딱 한 번만 호출되고, 이후에는 DB에 저장된
값을 그대로 내려준다 (menu_insights.py와 같은 claim 패턴):
  1) INSERT ... ON CONFLICT DO NOTHING 으로 status='pending' 행 선점 시도
  2) 선점 성공 → 이 요청이 AI를 호출하고 status='done'으로 저장
  3) 선점 실패 → 기존 행 조회
     - done이고 video_hash가 같으면 → 캐시 그대로 반환 (AI 재호출 없음)
     - video_hash가 다르면(영상 목록이 실제로 바뀜) → 한 번만 재생성
     - pending이면 → 다른 요청이 생성 중이므로 짧게 폴링

영상 정보는 클라이언트가 보내는 것이 아니라, 백엔드가 이미 갖고 있는 youtube_caches에서
읽어온다 — 프론트는 어떤 검색어로 찾았는지(queries)만 알려주면 된다.
"""
import asyncio
import hashlib
import logging
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import VideoEatingGuide, YoutubeCache
from .ai_provider import get_ai_provider

logger = logging.getLogger("video_guides")

_POLL_INTERVAL = 0.3
_POLL_TIMEOUT = 15.0
_MAX_VIDEOS_PER_DISH = 3


class AIGenerationTimeout(Exception):
    """다른 요청이 진행 중인 AI 생성이 타임아웃 내에 끝나지 않았을 때"""


_EMPTY = {"summary": "", "topMethod": None, "methods": []}


async def get_or_generate_video_guide(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    meal_date: date,
    queries: list[dict],
) -> dict:
    """queries: [{"dish": str, "query": str}] — query는 유튜브 검색에 쓴 검색어"""
    dish_videos = await _collect_videos(db, queries)
    if not dish_videos:
        # 영상이 하나도 없으면 AI에게 줄 근거가 없다 — 호출하지 않고 빈 값을 돌려준다.
        return _EMPTY

    video_hash = _hash_videos(dish_videos)

    if await _try_claim(db, office_code, school_code, meal_date, video_hash):
        return await _generate_and_save(db, office_code, school_code, meal_date, video_hash, dish_videos)

    row = await _fetch_row(db, office_code, school_code, meal_date)
    if row is not None and row.status == "done" and row.video_hash == video_hash:
        return _serialize(row)

    if row is not None and row.status != "pending":
        # 영상 목록이 바뀌었거나(해시 불일치) 지난번 생성이 실패한 경우에만 다시 만든다.
        if await _try_reclaim(db, office_code, school_code, meal_date, video_hash):
            return await _generate_and_save(db, office_code, school_code, meal_date, video_hash, dish_videos)

    return await _wait_for_done(db, office_code, school_code, meal_date, video_hash)


async def _collect_videos(db: AsyncSession, queries: list[dict]) -> list[dict]:
    """프론트가 검색에 쓴 검색어로 youtube_caches에서 영상 정보를 읽어온다."""
    dish_videos: list[dict] = []
    for item in queries:
        query = item["query"]
        result = await db.execute(select(YoutubeCache).where(YoutubeCache.query == query))
        row = result.scalar_one_or_none()
        if row is None or not row.payload:
            continue
        videos = [
            {
                "title": v.get("title", ""),
                "channelTitle": v.get("channelTitle", ""),
                "description": v.get("description", ""),
            }
            for v in row.payload[:_MAX_VIDEOS_PER_DISH]
        ]
        if videos:
            dish_videos.append({"dish": item["dish"], "videos": videos})
    return dish_videos


def _hash_videos(dish_videos: list[dict]) -> str:
    parts = []
    for item in dish_videos:
        parts.append(item["dish"])
        parts.extend(v.get("title", "") for v in item["videos"])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


async def _try_claim(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date, video_hash: str
) -> bool:
    stmt = (
        insert(VideoEatingGuide)
        .values(
            office_code=office_code,
            school_code=school_code,
            meal_date=meal_date,
            video_hash=video_hash,
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=["office_code", "school_code", "meal_date"])
        .returning(VideoEatingGuide.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.first() is not None


async def _try_reclaim(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date, video_hash: str
) -> bool:
    """done인데 영상이 바뀌었거나 failed인 행만 다시 선점한다 (pending은 건드리지 않는다)."""
    stmt = (
        update(VideoEatingGuide)
        .where(
            VideoEatingGuide.office_code == office_code,
            VideoEatingGuide.school_code == school_code,
            VideoEatingGuide.meal_date == meal_date,
            VideoEatingGuide.status != "pending",
            VideoEatingGuide.video_hash != video_hash,
        )
        .values(status="pending", video_hash=video_hash)
        .returning(VideoEatingGuide.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    if result.first() is not None:
        return True

    # 해시는 같은데 지난번 생성이 실패한 경우
    retry = (
        update(VideoEatingGuide)
        .where(
            VideoEatingGuide.office_code == office_code,
            VideoEatingGuide.school_code == school_code,
            VideoEatingGuide.meal_date == meal_date,
            VideoEatingGuide.status == "failed",
        )
        .values(status="pending", video_hash=video_hash)
        .returning(VideoEatingGuide.id)
    )
    result = await db.execute(retry)
    await db.commit()
    return result.first() is not None


async def _fetch_row(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date
) -> VideoEatingGuide | None:
    stmt = select(VideoEatingGuide).where(
        VideoEatingGuide.office_code == office_code,
        VideoEatingGuide.school_code == school_code,
        VideoEatingGuide.meal_date == meal_date,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _wait_for_done(
    db: AsyncSession, office_code: str, school_code: str, meal_date: date, video_hash: str
) -> dict:
    elapsed = 0.0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        db.expire_all()
        row = await _fetch_row(db, office_code, school_code, meal_date)
        if row is None:
            break
        if row.status == "done" and row.video_hash == video_hash:
            return _serialize(row)
        if row.status == "failed":
            break
    raise AIGenerationTimeout(f"{office_code}/{school_code}/{meal_date} 식사법 생성 대기 타임아웃")


async def _generate_and_save(
    db: AsyncSession,
    office_code: str,
    school_code: str,
    meal_date: date,
    video_hash: str,
    dish_videos: list[dict],
) -> dict:
    provider = get_ai_provider()

    try:
        content = await provider.summarize_eating_methods(dish_videos)
    except Exception:
        logger.exception("AI 식사법 요약 실패: %s/%s/%s", office_code, school_code, meal_date)
        await db.execute(
            update(VideoEatingGuide)
            .where(
                VideoEatingGuide.office_code == office_code,
                VideoEatingGuide.school_code == school_code,
                VideoEatingGuide.meal_date == meal_date,
            )
            .values(status="failed")
        )
        await db.commit()
        raise

    await db.execute(
        update(VideoEatingGuide)
        .where(
            VideoEatingGuide.office_code == office_code,
            VideoEatingGuide.school_code == school_code,
            VideoEatingGuide.meal_date == meal_date,
        )
        .values(
            status="done",
            video_hash=video_hash,
            content=content,
            model=provider.name,
            generated_at=func.now(),
        )
    )
    await db.commit()
    logger.info("식사법 생성 완료: %s/%s/%s", office_code, school_code, meal_date)

    row = await _fetch_row(db, office_code, school_code, meal_date)
    return _serialize(row)


def _serialize(row: VideoEatingGuide) -> dict:
    content = row.content or {}
    return {
        "summary": content.get("summary", ""),
        "topMethod": content.get("topMethod"),
        "methods": content.get("methods", []),
        "model": row.model,
        "generatedAt": row.generated_at.isoformat() if row.generated_at else None,
    }

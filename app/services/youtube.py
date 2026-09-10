"""
유튜브 먹방 영상 검색 (YouTube Data API v3)
─────────────────────────────────────────────────────────────
검색어당 유튜브 API는 최초 1회만 호출하고, 결과는 youtube_caches 테이블에
저장해 이후 접속자에게는 DB 값을 그대로 내려준다.
(search.list는 1회 100유닛, 무료 할당량은 하루 10,000유닛)

YOUTUBE_API_KEY가 비어 있으면 예외 대신 빈 리스트를 반환한다 —
키가 없어도 페이지가 "영상 준비 중" 상태로 정상 동작하게 하기 위함
(ai_provider.py의 StubAIProvider 폴백과 같은 방침).
"""
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import YoutubeCache

logger = logging.getLogger("youtube")

_MAX_RESULTS_LIMIT = 5
_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


async def search_videos(db: AsyncSession, query: str, max_results: int = 1) -> list[dict]:
    """검색어에 맞는 영상 목록을 반환한다 (캐시 우선, 없으면 1회 조회 후 저장)."""
    max_results = max(1, min(max_results, _MAX_RESULTS_LIMIT))

    cached = await _fetch_cache(db, query)
    if cached is not None:
        return cached[:max_results]

    if not settings.youtube_api_key:
        logger.warning("YOUTUBE_API_KEY가 비어 있어 영상 검색을 건너뜁니다: %s", query)
        return []

    videos = await _search_from_api(query, _MAX_RESULTS_LIMIT)
    if not videos:
        return []

    await db.execute(
        insert(YoutubeCache)
        .values(query=query, payload=videos)
        .on_conflict_do_nothing(index_elements=["query"])
    )
    await db.commit()
    logger.info("유튜브 검색 결과 최초 저장: %s (%d건)", query, len(videos))
    return videos[:max_results]


async def _fetch_cache(db: AsyncSession, query: str) -> list[dict] | None:
    result = await db.execute(select(YoutubeCache).where(YoutubeCache.query == query))
    row = result.scalar_one_or_none()
    return row.payload if row else None


async def _search_from_api(query: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        search = await _get(
            client,
            "search",
            {
                "part": "snippet",
                "type": "video",
                "q": query,
                "maxResults": max_results,
                "regionCode": "KR",
                "relevanceLanguage": "ko",
            },
        )
        video_ids = [
            item["id"]["videoId"]
            for item in search.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        # search.list에는 길이/조회수가 없어 videos.list로 보강한다 (1유닛)
        details = await _get(
            client,
            "videos",
            {"part": "snippet,contentDetails,statistics", "id": ",".join(video_ids)},
        )

    return [_serialize(item) for item in details.get("items", [])]


async def _get(client: httpx.AsyncClient, endpoint: str, params: dict) -> dict:
    url = f"{settings.youtube_base_url}/{endpoint}"
    logger.info("YouTube API 요청: endpoint=%s params=%s", endpoint, params)  # key는 제외
    response = await client.get(url, params={**params, "key": settings.youtube_api_key})
    response.raise_for_status()
    return response.json()


def _serialize(item: dict) -> dict:
    snippet = item.get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})
    thumb = thumbnails.get("medium") or thumbnails.get("default") or {}
    video_id = item.get("id", "")

    return {
        "videoId": video_id,
        "title": snippet.get("title", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "thumbnail": thumb.get("url", ""),
        "duration": _format_duration(item.get("contentDetails", {}).get("duration", "")),
        "views": _format_views(item.get("statistics", {}).get("viewCount")),
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _format_duration(iso_duration: str) -> str:
    """ISO8601 재생시간("PT6M45S")을 "6:45" 형태로 바꾼다."""
    match = _DURATION_RE.fullmatch(iso_duration or "")
    if not match:
        return ""
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_views(view_count: str | None) -> str:
    """조회수를 한국식 표기("5.2만")로 바꾼다."""
    if view_count is None:
        return ""
    count = int(view_count)
    if count >= 100_000_000:
        return f"{count / 100_000_000:.1f}억"
    if count >= 10_000:
        return f"{count / 10_000:.1f}만"
    return f"{count:,}"

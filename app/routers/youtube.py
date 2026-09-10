"""
유튜브 영상 검색 라우터
─────────────────────────────────────────────────────────────
API 키를 브라우저에 노출하지 않도록 서버가 대신 호출하고, 검색어별로 1회만
호출한 뒤 DB 캐시를 내려줍니다 (app/services/youtube.py 참고).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services import youtube

logger = logging.getLogger("youtube")

router = APIRouter()


@router.get("/youtube/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="검색어"),
    max: int = Query(1, ge=1, le=5, description="가져올 영상 수"),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await youtube.search_videos(db, q, max)
    except httpx.HTTPStatusError as e:
        logger.exception("YouTube API 호출 실패")
        raise HTTPException(status_code=502, detail=f"YouTube API 호출 실패: {e}")
    except httpx.RequestError as e:
        logger.exception("YouTube API 연결 실패")
        raise HTTPException(status_code=502, detail=f"YouTube API 연결 실패: {e}")

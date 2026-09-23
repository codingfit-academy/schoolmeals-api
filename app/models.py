"""
SQLAlchemy 모델 예시
─────────────────────────────────────────────────────────────
여기에 테이블 모델을 추가하세요.
앱 시작 시 main.py의 lifespan에서 테이블이 자동 생성됩니다.
"""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Item(Base):
    """예시 모델 — 필요에 맞게 수정하거나 삭제하세요."""
    __tablename__ = "items"

    id: Mapped[int]          = mapped_column(Integer, primary_key=True)
    title: Mapped[str]       = mapped_column(String(100), nullable=False)
    content: Mapped[str]     = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class School(Base):
    """NEIS 학교기본정보 배치 수집 결과 (매주 평일 새벽 1시 upsert)."""
    __tablename__ = "schools"
    __table_args__ = (UniqueConstraint("office_code", "school_code", name="uq_schools_code"),)

    id: Mapped[int]           = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]  = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]  = mapped_column(String(10), nullable=False, index=True)
    region: Mapped[str]       = mapped_column(String(20), nullable=False)
    name: Mapped[str]         = mapped_column(String(100), nullable=False)
    kind: Mapped[str]         = mapped_column(String(20), nullable=True)
    address: Mapped[str]      = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meal(Base):
    """NEIS 급식식단정보 배치 수집 결과 (매주 평일 새벽 1시 upsert).

    School과 FK로 엮지 않고 office_code/school_code 자연키만 사용한다 —
    배치가 학교/급식을 독립적으로 upsert할 수 있게 하기 위함
    (하나가 실패해도 나머지는 계속 진행).
    """
    __tablename__ = "meals"
    __table_args__ = (
        UniqueConstraint(
            "office_code", "school_code", "meal_date", "meal_type",
            name="uq_meals_school_date_type",
        ),
    )

    id: Mapped[int]             = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]    = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]    = mapped_column(String(10), nullable=False, index=True)
    meal_date: Mapped[date]     = mapped_column(Date, nullable=False, index=True)
    meal_type: Mapped[str]      = mapped_column(String(2), nullable=False)  # 1=조식 2=중식 3=석식
    menu_text: Mapped[str]      = mapped_column(Text, nullable=True)
    calorie_info: Mapped[str]   = mapped_column(String(50), nullable=True)
    nutrition_info: Mapped[str] = mapped_column(Text, nullable=True)
    origin_info: Mapped[str]    = mapped_column(Text, nullable=True)
    raw: Mapped[dict]           = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MealIngestState(Base):
    """학교별 급식 수집 구간 기록 — "어디부터 어디까지 이미 가져왔는지".

    요청 구간이 covered_from~covered_to 안에 들어오면 NEIS를 다시 호출하지 않고
    DB만 읽는다 (app/services/ingest.py 참고).
    """
    __tablename__ = "meal_ingest_states"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", name="uq_meal_ingest_states_code"),
    )

    id: Mapped[int]             = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]    = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]    = mapped_column(String(10), nullable=False, index=True)
    covered_from: Mapped[date]  = mapped_column(Date, nullable=False)
    covered_to: Mapped[date]    = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class YoutubeCache(Base):
    """검색어별 유튜브 결과 캐시 — 검색어당 유튜브 API는 최초 1회만 호출한다.

    YouTube Data API는 search.list 1회당 100유닛(일 10,000유닛 무료)이라
    한 번 받아온 결과는 그대로 재사용한다.
    """
    __tablename__ = "youtube_caches"
    __table_args__ = (UniqueConstraint("query", name="uq_youtube_caches_query"),)

    id: Mapped[int]        = mapped_column(Integer, primary_key=True)
    query: Mapped[str]     = mapped_column(String(200), nullable=False)
    payload: Mapped[list]  = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FoodLike(Base):
    """급식 메뉴 '찜' 카운터 — 랜딩페이지 '이번 달 BEST' 순위의 실제 근거.

    문구가 '이번 달'이므로 year_month(YYYY-MM) 단위로 집계한다.
    """
    __tablename__ = "food_likes"
    __table_args__ = (UniqueConstraint("slug", "year_month", name="uq_food_likes_slug_month"),)

    id: Mapped[int]         = mapped_column(Integer, primary_key=True)
    slug: Mapped[str]       = mapped_column(String(50), nullable=False, index=True)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    count: Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SchoolMealLike(Base):
    """학교 × 날짜별 급식 '좋아요' 카운터 — 랜딩페이지 '이번 달 가장 인기있는 급식 학교'의 근거.

    문구가 '이번 달'이므로 조회 시 meal_date를 그 달 범위로 걸러 집계한다.
    중복 클릭 방지는 서버가 아니라 프론트(localStorage, 학교×날짜 단위)가 담당한다.
    """
    __tablename__ = "school_meal_likes"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", "meal_date", name="uq_school_meal_likes_code_date"),
    )

    id: Mapped[int]          = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    meal_date: Mapped[date]  = mapped_column(Date, nullable=False, index=True)
    count: Mapped[int]       = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VideoEatingGuide(Base):
    """학교 × 날짜별 '유튜버들이 가장 추천하는 식사법' 캐시 (/menu 페이지).

    그 날 메뉴로 찾은 유튜브 먹방 영상들의 제목·설명을 AI가 읽고, 사람들이 실제로
    어떻게 먹는지를 정리한다. 여러 유튜버가 똑같이 말하는 방법일수록 '가장 추천하는
    식사법'으로 앞에 세운다.

    video_hash로 영상 목록이 바뀌었는지 감지한다 — 영상이 그대로면 같은 학교·같은
    날짜에 다시 들어와도 AI를 재호출하지 않는다. status로 동시 요청의 중복 AI 호출을
    막는다 (app/services/video_guides.py 참고):
    pending(생성 중) → done(캐시 완료) / failed(재시도 가능).
    """
    __tablename__ = "video_eating_guides"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", "meal_date", name="uq_video_eating_guides_code_date"),
    )

    id: Mapped[int]            = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    meal_date: Mapped[date]    = mapped_column(Date, nullable=False, index=True)
    video_hash: Mapped[str]    = mapped_column(String(64), nullable=False)
    status: Mapped[str]        = mapped_column(String(10), nullable=False, default="pending")
    content: Mapped[dict]      = mapped_column(JSON, nullable=True)
    model: Mapped[str]         = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MenuVote(Base):
    """메뉴 투표 카운터 — 투표 페이지가 '이번 주' 기준이므로 주 단위로 집계한다."""
    __tablename__ = "menu_votes"
    __table_args__ = (UniqueConstraint("option_key", "iso_week", name="uq_menu_votes_option_week"),)

    id: Mapped[int]         = mapped_column(Integer, primary_key=True)
    option_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    iso_week: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    count: Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MenuInsight(Base):
    """학교 × 날짜별 AI 메뉴 분석 캐시 (인기 메뉴 / 먹는 팁 / 건강 포인트).

    그 학교의 그 날짜 급식 페이지에 최초 접속했을 때만 AI를 호출하고, 이후
    같은 학교·같은 날짜 방문자에게는 이 캐시를 그대로 내려준다. status로
    동시 요청의 중복 AI 호출을 막는다 (app/services/menu_insights.py 참고):
    pending(생성 중) → done(캐시 완료) / failed(재시도 가능).
    """
    __tablename__ = "menu_insights"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", "meal_date", name="uq_menu_insights_code_date"),
    )

    id: Mapped[int]            = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    meal_date: Mapped[date]    = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str]        = mapped_column(String(10), nullable=False, default="pending")
    content: Mapped[dict]      = mapped_column(JSON, nullable=True)
    model: Mapped[str]         = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MealAllergenNote(Base):
    """학교 × 날짜별 AI 알레르기 보완 정보 캐시 (/calendar 페이지).

    NEIS 공식 알레르기 코드(1~19)는 학교가 직접 태깅한 값이라 실제로 들어있어도
    누락되는 경우가 있다 (예: '삼치된장박이구이'에 생선류 알레르기 코드가 빠진
    실제 사례로 확인됨). AI가 메뉴 이름만 보고 확실히 알 수 있는 경우에 한해
    놓쳤을 수 있는 알레르기 성분을 추정해 보완한다 — 참고용이며 NEIS 공식
    코드를 대체하지 않는다 (프론트에서도 별도로 표시).

    menu_hash로 그 날짜의 메뉴 내용이 바뀌었는지 감지한다 — 메뉴가 그대로면
    달력을 다시 봐도(월 이동 후 되돌아와도) AI를 재호출하지 않는다. status로
    동시 요청의 중복 AI 호출을 막는다 (app/services/allergen_notes.py 참고):
    pending(생성 중) → done(캐시 완료) / failed(재시도 가능).
    """
    __tablename__ = "meal_allergen_notes"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", "meal_date", name="uq_meal_allergen_notes_code_date"),
    )

    id: Mapped[int]            = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    meal_date: Mapped[date]    = mapped_column(Date, nullable=False, index=True)
    menu_hash: Mapped[str]     = mapped_column(String(64), nullable=False)
    status: Mapped[str]        = mapped_column(String(10), nullable=False, default="pending")
    content: Mapped[list]      = mapped_column(JSON, nullable=True)
    model: Mapped[str]         = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

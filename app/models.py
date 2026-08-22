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


class SchoolAiContent(Base):
    """학교별 AI 소개 캐시 — 학교당 1행, 최초 방문 시 1회만 생성한다.

    status로 동시 요청의 중복 AI 호출을 막는다 (app/services/school_ai.py 참고):
    pending(생성 중) → done(캐시 완료) / failed(재시도 가능).
    """
    __tablename__ = "school_ai_contents"
    __table_args__ = (
        UniqueConstraint("office_code", "school_code", name="uq_school_ai_contents_code"),
    )

    id: Mapped[int]            = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    school_code: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str]        = mapped_column(String(10), nullable=False, default="pending")
    content: Mapped[dict]      = mapped_column(JSON, nullable=True)
    model: Mapped[str]         = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

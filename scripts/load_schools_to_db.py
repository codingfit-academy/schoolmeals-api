"""
data/schools_seoul_gyeonggi.json (scripts/fetch_schools.py 결과)를 schools 테이블에 upsert하는 스크립트.

사용법:
    PYTHONPATH=. .venv\\Scripts\\python.exe scripts\\load_schools_to_db.py
"""
import asyncio
import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.models import School

INPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "schools_seoul_gyeonggi.json"


async def main() -> None:
    rows = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

    async with SessionLocal() as db:
        for row in rows:
            values = {
                "region": row["region"],
                "name": row["name"],
                "kind": row.get("kind"),
                "address": row.get("address"),
            }
            stmt = (
                insert(School)
                .values(office_code=row["officeCode"], school_code=row["schoolCode"], **values)
                .on_conflict_do_update(
                    index_elements=["office_code", "school_code"],
                    set_=values,
                )
            )
            await db.execute(stmt)

        await db.commit()

    print(f"저장 완료: {len(rows)}건을 schools 테이블에 upsert")


if __name__ == "__main__":
    asyncio.run(main())

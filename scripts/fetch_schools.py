"""
서울/경기 초·중·고 학교 목록을 NEIS API에서 받아와 JSON 파일로 저장하는 1회성 스크립트.

사용법:
    .venv\\Scripts\\python.exe scripts\\fetch_schools.py
"""
import asyncio
import json
from pathlib import Path

from app.services import neis

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "schools_seoul_gyeonggi.json"
TARGET_KINDS = {"초등학교", "중학교", "고등학교"}


async def main() -> None:
    all_schools = []

    for region, office_code in neis.REGION_OFFICE_CODES.items():
        rows = await neis.fetch_all_schools(office_code)
        for row in rows:
            kind = row.get("SCHUL_KND_SC_NM")
            if kind not in TARGET_KINDS:
                continue
            all_schools.append({
                "region": region,
                "officeCode": row.get("ATPT_OFCDC_SC_CODE"),
                "schoolCode": row.get("SD_SCHUL_CODE"),
                "name": row.get("SCHUL_NM"),
                "engName": row.get("ENG_SCHUL_NM"),
                "kind": kind,
                "address": row.get("ORG_RDNMA"),
                "zipCode": (row.get("ORG_RDNZC") or "").strip(),
                "tel": row.get("ORG_TELNO"),
                "homepage": row.get("HMPG_ADRES"),
            })
        print(f"{region}: {len(rows)}건 수신 (초중고 필터 후 누적 {len(all_schools)}건)")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(all_schools, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"저장 완료: {OUTPUT_PATH} (총 {len(all_schools)}건)")


if __name__ == "__main__":
    asyncio.run(main())

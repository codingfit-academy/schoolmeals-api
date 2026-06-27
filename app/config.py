"""
중앙 설정 (config)
─────────────────────────────────────────────────────────────
모든 환경변수를 여기 한 곳에서 읽습니다. 나머지 코드는 이 파일을 import 하세요.

    from .config import settings
    settings.port, settings.db_url, settings.public ...

env 주입 경로:
  - PORT / DB_*  : 서버 provision 과정에서 .env 에 자동 기록
                   (로컬은 .env 를 직접 만들어 사용 — .env.example 참고)
  - public.*     : 선생님 공유 키(config/maps.env) → api 컨테이너 →
                   GET /config 로 프론트에 노출 (브라우저에 보여도 되는 값만!)

새 환경변수가 필요하면 아래에 추가하세요.
"""
import os


class Settings:
    port: int = int(os.getenv("PORT", "8000"))

    # ── DB (provision 자동 주입) ──────────────────────────────
    db_host: str = os.getenv("DB_HOST", "postgres")
    db_port: str = os.getenv("DB_PORT", "5432")
    db_name: str = os.getenv("DB_NAME", "")
    db_user: str = os.getenv("DB_USER", "")
    db_pass: str = os.getenv("DB_PASS", "")

    @property
    def db_url(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{self.db_user}:{self.db_pass}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── 프론트에 내려줄 공개 값 (GET /config) ─────────────────
    #    ⚠ 브라우저에 노출됩니다. 공개해도 되는 값만 넣으세요.
    @property
    def public(self) -> dict:
        return {
            "naverMapsClientId": os.getenv("NAVER_MAPS_CLIENT_ID", ""),
            "kakaoMapsAppKey":   os.getenv("KAKAO_MAPS_APP_KEY", ""),
            # 새 공개 키는 여기 한 줄 추가
        }


settings = Settings()

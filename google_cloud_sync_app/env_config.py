from __future__ import annotations
"""환경변수 및 .env 설정 로더."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 실행에 필요한 설정 묶음."""

    google_project_id: str
    google_translate_location: str
    source_language_code: str
    target_language_code: str


def _load_dotenv_file(dotenv_path: Path) -> None:
    """.env 파일을 읽어 os.environ에 기본값으로 주입합니다.

    이미 OS에 설정된 환경변수는 덮어쓰지 않습니다.
    """
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # bash 스타일 export KEY=VALUE 구문도 허용
        if line.lower().startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # "value" 또는 'value' 형태의 따옴표 제거
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def load_app_config(base_dir: Path | None = None) -> AppConfig:
    """.env + OS 환경변수를 반영해 실행 설정을 반환합니다."""
    app_dir = base_dir or Path(__file__).resolve().parent
    _load_dotenv_file(app_dir / ".env")

    return AppConfig(
        google_project_id=os.getenv("GOOGLE_CLOUD_PROJECT", "").strip(),
        google_translate_location=os.getenv("GOOGLE_TRANSLATE_LOCATION", "global").strip() or "global",
        source_language_code=os.getenv("SOURCE_LANGUAGE_CODE", "en-US").strip() or "en-US",
        target_language_code=os.getenv("TARGET_LANGUAGE_CODE", "ko").strip() or "ko",
    )

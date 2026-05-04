import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account


PROJECT_ROOT = Path(__file__).resolve().parent


def load_google_env():
    # 어느 파일에서 호출하든 프로젝트 루트의 .env를 같은 기준으로 읽는다.
    load_dotenv(PROJECT_ROOT / ".env")


def get_google_project_id() -> str:
    load_google_env()

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("환경변수 GCP_PROJECT_ID 가 설정되어 있지 않습니다.")

    return project_id


def get_google_credentials_path() -> Path:
    load_google_env()

    credentials_file = os.getenv("GCP_CREDENTIALS_FILE")

    if not credentials_file:
        raise RuntimeError(
            "GCP_CREDENTIALS_FILE is not set. "
            "Add the service account JSON path to your .env file."
        )

    credentials_path = Path(credentials_file).expanduser()
    if not credentials_path.is_absolute():
        # .env에는 보통 credentials/key.json 같은 상대 경로를 적으므로 루트 기준으로 보정한다.
        credentials_path = PROJECT_ROOT / credentials_path

    return credentials_path


def load_google_credentials():
    credentials_path = get_google_credentials_path()

    if not credentials_path.exists():
        raise RuntimeError(f"Google service account JSON was not found: {credentials_path}")

    # STT와 번역 클라이언트가 같은 인증 객체 생성 방식을 공유한다.
    return service_account.Credentials.from_service_account_file(credentials_path)

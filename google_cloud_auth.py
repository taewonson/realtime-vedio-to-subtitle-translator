# .env와 서비스 계정 JSON을 읽어 Google Cloud 프로젝트 ID와 인증 객체를 제공합니다.
import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account

# 현재 파일(google_cloud_auth.py)이 위치한 디렉토리의 절대 경로를 프로젝트 루트로 지정함
# 이를 통해 스크립트 실행 위치가 달라져도 항상 올바른 경로를 참조할 수 있음
PROJECT_ROOT = Path(__file__).resolve().parent


def load_google_env():
    """
    프로젝트 최상단에 있는 .env 파일을 찾아 환경변수를 로드하는 함수.
    os.getcwd() 대신 PROJECT_ROOT를 사용하여, 어느 폴더에서 메인 스크립트를 실행하든
    동일한 .env 파일을 읽어오도록 강제함.
    """
    load_dotenv(PROJECT_ROOT / ".env")


def get_google_project_id() -> str:
    """
    환경변수에서 GCP_PROJECT_ID 값을 가져옴.
    주로 번역 API 등에서 해당 프로젝트에 속한 자원(location 등)을 참조할 때 사용됨.
    """
    load_google_env()

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("환경변수 GCP_PROJECT_ID 가 설정되어 있지 않습니다.")

    return project_id


def get_google_credentials_path(env_name: str = "GCP_CREDENTIALS_FILE") -> Path:
    """
    GCP 서비스 어카운트(Service Account) JSON 키 파일의 경로를 확보하고 보정함.
    상대 경로가 입력되었을 경우, 프로젝트 루트 폴더 기준으로 절대 경로를 만들어 반환함.
    """
    load_google_env()

    # .env 파일에 정의된 JSON 키 파일의 경로를 가져옴 (예: credentials/key.json)
    credentials_file = os.getenv(env_name)

    if not credentials_file:
        raise RuntimeError(
            f"{env_name} is not set. "
            "Add the service account JSON path to your .env file."
        )

    # '~/' 와 같은 홈 디렉토리 기호가 있으면 풀어줌
    credentials_path = Path(credentials_file).expanduser()
    
    # 경로가 절대 경로가 아닌 경우 (상대 경로인 경우)
    if not credentials_path.is_absolute():
        # 프로젝트 루트 경로를 앞에 붙여서 완전한 절대 경로로 조립함
        credentials_path = PROJECT_ROOT / credentials_path

    return credentials_path


def load_google_credentials(env_name: str = "GCP_CREDENTIALS_FILE"):
    """
    보정된 경로를 바탕으로 Google Cloud 라이브러리가 사용할 수 있는 인증 객체(Credentials)를 생성함.
    STT 모듈과 번역 모듈이 각각 이 함수를 호출하여 권한을 획득함.
    """
    credentials_path = get_google_credentials_path(env_name)

    # 파일이 해당 경로에 실제로 존재하는지 최종 검증
    if not credentials_path.exists():
        raise RuntimeError(f"Google service account JSON was not found: {credentials_path}")

    # 구글 oauth2 라이브러리를 사용하여 JSON 키 파일로부터 인증 객체를 로드 및 반환
    credentials = service_account.Credentials.from_service_account_file(credentials_path)
    if credentials.requires_scopes:
        credentials = credentials.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
    return credentials

# .env와 서비스 계정 JSON을 읽어 Google Cloud 프로젝트 ID와 인증 객체를 제공합니다.
import argparse
import base64
import json
import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 현재 파일(google_cloud_auth.py)이 위치한 디렉토리의 절대 경로를 기본 프로젝트 루트로 지정함
# PyInstaller로 묶인 경우에는 임시 추출 루트(sys._MEIPASS)를 우선 사용한다.
PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_CREDENTIALS_PASSPHRASE_ENV = "GCP_CREDENTIALS_PASSPHRASE"
_DEFAULT_VOCAB_PASSPHRASE_ENV = "GCP_VOCAB_PASSPHRASE"
_ENCRYPTED_FILE_SUFFIXES = {".enc", ".gcpenc"}


def get_runtime_root() -> Path:
    """개발 실행과 PyInstaller 실행을 모두 고려한 리소스 루트를 반환합니다."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return PROJECT_ROOT


def resolve_bundled_tool_path(tool_name: str, relative_dir: str = "ffmpeg/bin") -> Path:
    """PyInstaller 번들 안의 외부 실행 파일 경로를 우선적으로 찾습니다."""
    runtime_tool = get_runtime_root() / relative_dir / tool_name
    if runtime_tool.exists():
        return runtime_tool
    return Path(tool_name)


def load_google_env():
    """
    프로젝트 최상단에 있는 .env 파일을 찾아 환경변수를 로드하는 함수.
    os.getcwd() 대신 PROJECT_ROOT를 사용하여, 어느 폴더에서 메인 스크립트를 실행하든
    동일한 .env 파일을 읽어오도록 강제함.
    """
    load_dotenv(get_runtime_root() / ".env")


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
        credentials_path = get_runtime_root() / credentials_path

    return credentials_path


def _get_credentials_passphrase(env_name: str, fallback_env_name: str | None = None) -> str:
    """암호화된 서비스 계정 파일을 해독하기 위한 passphrase를 가져옵니다."""
    load_google_env()

    passphrase = os.getenv(env_name)
    if not passphrase and fallback_env_name:
        passphrase = os.getenv(fallback_env_name)

    if not passphrase:
        raise RuntimeError(
            f"{env_name} is not set. Add the decryption passphrase to your .env file."
        )

    return passphrase


def _derive_aes_key(passphrase: str, salt: bytes) -> bytes:
    """passphrase와 salt로 AES-256 키를 파생합니다."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _decrypt_credentials_payload(payload: bytes, passphrase: str) -> dict:
    """암호화된 JSON payload를 복호화하여 서비스 계정 정보 dict를 반환합니다."""
    envelope = json.loads(payload.decode("utf-8"))

    try:
        salt = base64.b64decode(envelope["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
    except KeyError as exc:
        raise RuntimeError("암호화된 인증 파일 형식이 올바르지 않습니다.") from exc

    key = _derive_aes_key(passphrase, salt)
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def _is_encrypted_credentials_file(credentials_path: Path) -> bool:
    """확장자 기준으로 암호화된 인증 파일 여부를 판단합니다."""
    return credentials_path.suffix.lower() in _ENCRYPTED_FILE_SUFFIXES


def load_google_credentials_from_path(
    credentials_path: Path,
    passphrase_env_name: str = _DEFAULT_CREDENTIALS_PASSPHRASE_ENV,
    fallback_passphrase_env_name: str | None = None,
):
    """지정한 경로에서 Google Cloud Credentials 객체를 로드합니다.

    평문 JSON과 암호화된 JSON 둘 다 지원합니다.
    """
    if not credentials_path.exists():
        raise RuntimeError(f"Google service account JSON was not found: {credentials_path}")

    if _is_encrypted_credentials_file(credentials_path):
        passphrase = _get_credentials_passphrase(passphrase_env_name, fallback_passphrase_env_name)
        encrypted_payload = credentials_path.read_bytes()
        credentials_info = _decrypt_credentials_payload(encrypted_payload, passphrase)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
    else:
        credentials = service_account.Credentials.from_service_account_file(credentials_path)

    if credentials.requires_scopes:
        credentials = credentials.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


def load_google_credentials(env_name: str = "GCP_CREDENTIALS_FILE"):
    """
    보정된 경로를 바탕으로 Google Cloud 라이브러리가 사용할 수 있는 인증 객체(Credentials)를 생성함.
    STT 모듈과 번역 모듈이 각각 이 함수를 호출하여 권한을 획득함.
    """
    credentials_path = get_google_credentials_path(env_name)
    return load_google_credentials_from_path(credentials_path)


def encrypt_google_credentials_file(
    source_path: Path,
    destination_path: Path,
    passphrase: str,
):
    """평문 서비스 계정 JSON을 암호화 파일로 변환합니다."""
    credentials_info = json.loads(source_path.read_text(encoding="utf-8"))
    plaintext = json.dumps(credentials_info, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_aes_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)

    envelope = {
        "version": 1,
        "algorithm": "AESGCM",
        "kdf": "PBKDF2HMAC-SHA256",
        "iterations": 390000,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    destination_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Google service account JSON encryption helper")
    subparsers = parser.add_subparsers(dest="command")

    encrypt_parser = subparsers.add_parser("encrypt", help="Encrypt a service account JSON file")
    encrypt_parser.add_argument("source", type=Path, help="Plain service account JSON path")
    encrypt_parser.add_argument("destination", type=Path, nargs="?", help="Encrypted output path")
    encrypt_parser.add_argument("--passphrase", required=True, help="Encryption passphrase")

    return parser


def _main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.command == "encrypt":
        destination = args.destination or args.source.with_suffix(args.source.suffix + ".enc")
        encrypt_google_credentials_file(args.source, destination, args.passphrase)
        print(f"Encrypted credentials written to: {destination}")
        return

    parser.print_help()


if __name__ == "__main__":
    _main()

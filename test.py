import os
from google.cloud import translate_v3 as translate
from google_cloud_auth import (
    get_google_credentials_path,
    get_google_project_id,
    load_google_credentials,
    load_google_env,
)

load_google_env()

print("GCP_PROJECT_ID =", get_google_project_id())
print("GCP_CREDENTIALS_FILE =", os.getenv("GCP_CREDENTIALS_FILE"))
credentials_path = get_google_credentials_path()
print("RESOLVED CREDENTIALS PATH =", credentials_path)
print("PATH EXISTS =", credentials_path.exists())

try:
    credentials = load_google_credentials()
    client = translate.TranslationServiceClient(credentials=credentials)
except Exception as exc:
    raise SystemExit(f"Google Cloud authentication check failed: {exc}") from exc

print("OK")

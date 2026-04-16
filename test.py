import os
from dotenv import load_dotenv
from google.cloud import translate

load_dotenv()

print("GCP_PROJECT_ID =", os.getenv("GCP_PROJECT_ID"))
print("GOOGLE_APPLICATION_CREDENTIALS =", os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
print("PATH EXISTS =", os.path.exists(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")))

client = translate.TranslationServiceClient()
print("OK")
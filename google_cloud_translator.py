import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import translate_v3 as translate
from google_cloud_auth import get_google_project_id, load_google_credentials


# ==========================================
# Google Cloud Translation 기반 번역 엔진
# - 기존 함수명/반환 구조 유지
# - 인증 정보 로딩은 google_cloud_auth.py에서 일괄 처리
# ==========================================

def _safe_print(message):
    # 진행 메시지 출력 실패가 번역 작업 자체를 중단시키지 않게 한다.
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def translate_subtitles(segments, status_callback=None):
    def update_status(msg, percent):
        _safe_print(msg)
        if status_callback:
            status_callback(msg, percent)

    project_id = get_google_project_id()
    location = os.getenv("GCP_TRANSLATE_LOCATION", "global")

    original_texts = [segment.text.strip() for segment in segments]
    if not original_texts:
        update_status("번역할 문장이 없습니다.", 95)
        return []

    update_status(f"[1/2] Google Cloud 다국어 번역 진행 중... (총 {len(original_texts)}문장)", 70)

    # STT와 같은 인증 헬퍼를 사용해 로컬 실행 경로 기준을 맞춘다.
    credentials = load_google_credentials()
    client = translate.TranslationServiceClient(credentials=credentials)
    parent = f"projects/{project_id}/locations/{location}"

    # UI에서 기대하는 키 유지
    target_langs = {
        "ko": "ko",
        "en": "en",
        "ja": "ja",
        "zh": "zh-CN",
        "de": "de",
    }

    translated_data = {
        "original": original_texts
    }

    # 한 번에 너무 많이 보내지 않도록 청크 분할
    batch_size = int(os.getenv("GCP_TRANSLATE_BATCH_SIZE", "100"))

    def translate_language(lang_key, lang_code):
        results = []

        for i in range(0, len(original_texts), batch_size):
            chunk = original_texts[i:i + batch_size]

            response = client.translate_text(
                request={
                    "parent": parent,
                    "contents": chunk,
                    "mime_type": "text/plain",
                    "target_language_code": lang_code,
                }
            )

            results.extend([item.translated_text for item in response.translations])

        return lang_key, results

    futures = []
    with ThreadPoolExecutor(max_workers=len(target_langs)) as executor:
        for lang_key, lang_code in target_langs.items():
            futures.append(executor.submit(translate_language, lang_key, lang_code))

        done_count = 0
        for future in as_completed(futures):
            lang_key, translated_texts = future.result()
            translated_data[lang_key] = translated_texts
            done_count += 1
            progress = 70 + int((done_count / len(target_langs)) * 20)
            update_status(f"  - Google Translate {lang_key} 번역 완료!", progress)

    update_status("[2/2] 최종 데이터 조립 중...", 95)

    subtitles = []
    for i, segment in enumerate(segments):
        texts_dict = {}
        for lang_key in ["original", "ko", "en", "ja", "zh", "de"]:
            texts_dict[lang_key] = translated_data.get(lang_key, original_texts)[i]

        subtitles.append({
            "start": segment.start,
            "end": segment.end,
            "texts": texts_dict
        })

    update_status("✅ 모든 번역 완료!\n", 100)
    return subtitles

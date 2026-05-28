# Google Cloud Translation API로 STT 자막 세그먼트를 여러 언어로 병렬 번역합니다.
"""Batch translation pipeline for subtitle segments."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import translate_v3 as translate
from google_cloud_auth import get_google_project_id, load_google_credentials
from language_config import SUBTITLE_TARGET_LANGS


# ==========================================
# Google Cloud Translation 기반 번역 엔진
# - 기존 함수명/반환 구조 유지
# - 인증 정보 로딩은 google_cloud_auth.py에서 일괄 처리
# - 다국어 번역 시 처리 속도를 높이기 위해 멀티스레딩(ThreadPoolExecutor) 사용
# ==========================================

def _safe_print(message):
    """
    Windows 터미널 환경에서 진행 메시지 출력 시 발생할 수 있는 인코딩 에러가
    번역 작업 자체를 중단시키지 않도록 방어하는 출력 함수입니다.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def translate_subtitles(segments, status_callback=None):
    """
    추출된 원본 자막 세그먼트 리스트를 받아, 여러 타겟 언어로 번역한 뒤
    시간 정보와 다국어 텍스트가 모두 포함된 딕셔너리 리스트를 반환합니다.
    """
    def update_status(msg, percent):
        # UI 프로그레스 바 및 상태 텍스트 업데이트용 콜백
        _safe_print(msg)
        if status_callback:
            status_callback(msg, percent)

    # GCP 프로젝트 ID 및 위치(location) 정보 로드
    project_id = get_google_project_id()
    location = os.getenv("GCP_TRANSLATE_LOCATION", "global")

    # 세그먼트 객체에서 번역할 순수 텍스트만 추출
    original_texts = [segment.text.strip() for segment in segments]
    if not original_texts:
        update_status("번역할 문장이 없습니다.", 95)
        return []

    update_status(f"[1/2] Google Cloud 다국어 번역 진행 중... (총 {len(original_texts)}문장)", 70)

    # STT와 동일한 인증 헬퍼를 사용하여 서비스 어카운트 권한 로드
    credentials = load_google_credentials()
    client = translate.TranslationServiceClient(credentials=credentials)
    parent = f"projects/{project_id}/locations/{location}"

    # 번역할 대상 언어 코드 매핑 (UI에서 기대하는 키값과 GCP 언어 코드를 맞춤)
    target_langs = SUBTITLE_TARGET_LANGS

    # 번역 결과를 담을 딕셔너리. 원본 텍스트는 미리 넣어둠
    translated_data = {
        "original": original_texts
    }
    output_lang_keys = ["original", *target_langs.keys()]

    # 한 번의 API 호출에 너무 많은 문장을 보내면 용량 제한에 걸릴 수 있으므로 청크 단위로 분할
    batch_size = int(os.getenv("GCP_TRANSLATE_BATCH_SIZE", "100"))

    def translate_language(lang_key, lang_code):
        """
        단일 언어에 대해 배치 사이즈만큼 텍스트를 나누어 번역 API를 호출하고 결과를 합치는 헬퍼 함수입니다.
        (멀티스레드 내부에서 실행됨)
        """
        results = []

        for i in range(0, len(original_texts), batch_size):
            chunk = original_texts[i:i + batch_size]

            # Google Cloud Translate V3 API 호출
            response = client.translate_text(
                request={
                    "parent": parent,
                    "contents": chunk,
                    "mime_type": "text/plain", # HTML 태그 등이 없으므로 일반 텍스트로 처리
                    "target_language_code": lang_code,
                }
            )

            # 응답에서 번역된 텍스트만 추출하여 결과 리스트에 추가
            results.extend([item.translated_text for item in response.translations])

        return lang_key, results

    # 다국어 번역을 동시에 처리하기 위해 스레드 풀 생성 (대상 언어 개수만큼 워커 할당)
    futures = []
    with ThreadPoolExecutor(max_workers=len(target_langs)) as executor:
        for lang_key, lang_code in target_langs.items():
            # 각 언어별 번역 작업을 스레드 풀에 제출
            futures.append(executor.submit(translate_language, lang_key, lang_code))

        done_count = 0
        # 먼저 완료되는 번역 작업부터 순차적으로 결과 수집
        for future in as_completed(futures):
            lang_key, translated_texts = future.result()
            translated_data[lang_key] = translated_texts
            
            # UI 진행률 업데이트 로직 (모든 언어가 완료될 때까지 70% ~ 90% 사이에서 증가)
            done_count += 1
            progress = 70 + int((done_count / len(target_langs)) * 20)
            update_status(f"  - Google Translate {lang_key} 번역 완료!", progress)

    update_status("[2/2] 최종 데이터 조립 중...", 95)

    # 원본 시간 정보(세그먼트)와 모든 언어의 번역 결과를 하나의 최종 리스트로 조립
    subtitles = []
    for i, segment in enumerate(segments):
        texts_dict = {}
        # 각 언어 키에 대해 i번째 번역 문장을 매핑
        for lang_key in output_lang_keys:
            # 만약 특정 언어 번역본이 누락되었다면 원본 텍스트를 기본값으로 사용 (방어적 코드)
            texts_dict[lang_key] = translated_data.get(lang_key, original_texts)[i]

        subtitles.append({
            "start": segment.start,
            "end": segment.end,
            "texts": texts_dict
        })

    update_status("✅ 모든 번역 완료!\n", 100)
    return subtitles

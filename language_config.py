# 언어 라벨, 번역 대상, TTS 언어 코드를 한곳에서 관리합니다.
"""Application-wide language metadata.

This module keeps language labels and API language codes consistent across
the PC UI, UDP service, subtitle translator, and LCD clients.
"""

LANG_LABELS = {
    "original": "원본",
    "ko": "한국어",
    "en": "영어",
    "ja": "일본어",
    "zh": "중국어",
    "de": "독일어",
}

SUBTITLE_TARGET_LANGS = {
    "ko": "ko",
    "en": "en",
    "ja": "ja",
    "zh": "zh-CN",
    "de": "de",
}

VOCAB_TRANSLATION_OPTIONS = {
    "한국어": "ko",
    "영어": "en",
    "일본어": "ja",
    "중국어": "zh-CN",
    "독일어": "de",
    "프랑스어": "fr",
    "스페인어": "es",
}

TTS_LANGUAGE_CODES = {
    "ko": "ko-KR",
    "en": "en-US",
    "ja": "ja-JP",
    "zh": "cmn-CN",
    "zh-CN": "cmn-CN",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
}

LCD_LANGUAGE_BUTTONS = [
    ("한국어", "ko"),
    ("영어", "en"),
    ("일본어", "ja"),
    ("독일어", "de"),
    ("원본", "original"),
]


def get_language_label(lang_code):
    """Return a Korean display label for a language code."""
    return LANG_LABELS.get(lang_code, lang_code)


def get_tts_language_code(lang_code, default_source_language="en-US"):
    """Return the Google Cloud TTS voice language code for an app language code."""
    if not lang_code or lang_code == "original":
        return default_source_language
    return TTS_LANGUAGE_CODES.get(lang_code, lang_code if "-" in lang_code else default_source_language)

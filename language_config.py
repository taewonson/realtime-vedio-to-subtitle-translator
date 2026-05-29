"""Application-wide language metadata.

This module keeps language labels and API language codes consistent across
the PC UI, UDP service, subtitle translator, and LCD clients.
"""

SUPPORTED_LANGUAGE_OPTIONS = [
    ("원본", "original"),
    ("한국어", "ko"),
    ("영어", "en"),
    ("일본어", "ja"),
    ("중국어", "zh-CN"),
    ("스페인어", "es"),
    ("프랑스어", "fr"),
    ("독일어", "de"),
    ("포르투갈어", "pt"),
    ("러시아어", "ru"),
    ("이탈리아어", "it"),
    ("베트남어", "vi"),
    ("인도네시아어", "id"),
    ("태국어", "th"),
    ("아랍어", "ar"),
    ("힌디어", "hi"),
]

TRANSLATION_LANGUAGE_OPTIONS = [
    (label, code)
    for label, code in SUPPORTED_LANGUAGE_OPTIONS
    if code != "original"
]

LANG_LABELS = {code: label for label, code in SUPPORTED_LANGUAGE_OPTIONS}
LANG_LABELS.update({"zh": "중국어"})

SUBTITLE_TARGET_LANGS = {
    "ko": "ko",
    "en": "en",
    "ja": "ja",
    "zh-CN": "zh-CN",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "pt": "pt",
    "ru": "ru",
    "it": "it",
    "vi": "vi",
    "id": "id",
    "th": "th",
    "ar": "ar",
    "hi": "hi",
}

VOCAB_TRANSLATION_OPTIONS = {label: code for label, code in TRANSLATION_LANGUAGE_OPTIONS}

TTS_LANGUAGE_CODES = {
    "ko": "ko-KR",
    "en": "en-US",
    "ja": "ja-JP",
    "zh": "cmn-CN",
    "zh-CN": "cmn-CN",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "pt": "pt-BR",
    "ru": "ru-RU",
    "it": "it-IT",
    "vi": "vi-VN",
    "id": "id-ID",
    "th": "th-TH",
    "ar": "ar-XA",
    "hi": "hi-IN",
}

LCD_LANGUAGE_OPTIONS = SUPPORTED_LANGUAGE_OPTIONS
LCD_LANGUAGE_LABEL_TO_CODE = {label: code for label, code in LCD_LANGUAGE_OPTIONS}

# Backward-compatible alias used by older code paths.
LCD_LANGUAGE_BUTTONS = LCD_LANGUAGE_OPTIONS


def get_language_label(lang_code):
    """Return a Korean display label for a language code."""
    return LANG_LABELS.get(lang_code, lang_code)


def get_tts_language_code(lang_code, default_source_language="en-US"):
    """Return the Google Cloud TTS voice language code for an app language code."""
    if not lang_code or lang_code == "original":
        return default_source_language
    return TTS_LANGUAGE_CODES.get(lang_code, lang_code if "-" in lang_code else default_source_language)

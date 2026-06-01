"""애플리케이션 전역에서 사용하는 언어 메타데이터를 정의합니다.

PC UI, UDP 서비스, 자막 번역기, LCD 클라이언트가 동일한 언어 라벨과
API 언어 코드를 공유하도록 중앙에서 관리합니다.
"""

# ==========================================
# 언어 옵션과 표시 라벨
# ==========================================

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

# 언어 코드 -> 한국어 라벨 매핑
LANG_LABELS = {code: label for label, code in SUPPORTED_LANGUAGE_OPTIONS}
LANG_LABELS.update({"zh": "중국어"})

# 화면 표기용 언어 코드와 실제 번역 API 코드가 다를 수 있어 별도 매핑을 둠
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

# 단어장 저장 창에서 사용할 번역 언어 선택지
VOCAB_TRANSLATION_OPTIONS = {label: code for label, code in TRANSLATION_LANGUAGE_OPTIONS}

# Google Cloud TTS는 언어별 표준 음성 코드를 요구하므로 변환 테이블을 분리함
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

# Google Speech-to-Text에서 사용할 언어 후보 코드.
# 번역/발음 지원 언어와 최대한 같은 범위를 재사용하되, STT가 요구하는 로케일 형식으로 둡니다.
STT_LANGUAGE_CODES = {
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

# 이전 코드 경로와의 호환을 위해 유지하는 별칭
LCD_LANGUAGE_BUTTONS = LCD_LANGUAGE_OPTIONS


def get_language_label(lang_code):
    """언어 코드에 대응하는 한국어 표시 라벨을 반환합니다."""
    return LANG_LABELS.get(lang_code, lang_code)


def get_tts_language_code(lang_code, default_source_language="en-US"):
    """앱 언어 코드를 Google Cloud TTS 음성 코드로 변환합니다."""
    if not lang_code or lang_code == "original":
        return default_source_language
    return TTS_LANGUAGE_CODES.get(lang_code, lang_code if "-" in lang_code else default_source_language)


def get_stt_language_candidates(source_language="en-US", alternative_languages=None):
    """STT 인식에 사용할 언어 후보를 중복 없이 반환합니다."""
    candidates = []

    def add_candidate(language_code):
        if language_code and language_code not in candidates:
            candidates.append(language_code)

    add_candidate(source_language)

    if alternative_languages:
        for language_code in alternative_languages:
            add_candidate(language_code)

    for _, language_code in SUPPORTED_LANGUAGE_OPTIONS:
        if language_code == "original":
            continue
        add_candidate(STT_LANGUAGE_CODES.get(language_code, language_code))

    return candidates

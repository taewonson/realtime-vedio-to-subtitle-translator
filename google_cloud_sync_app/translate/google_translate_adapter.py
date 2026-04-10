from __future__ import annotations
"""경량 메모이제이션을 적용한 Google Cloud Translation 어댑터."""

from functools import lru_cache

from google.cloud import translate_v3


class GoogleTranslateAdapter:
    """Cloud Translation Advanced(v3)를 감싼 얇은 래퍼.

    스트리밍 자막은 사용자가 seek하거나 짧은 구간을 반복 재생할 때
    동일한 final 문장이 다시 나오기 쉬워 최근 번역 결과를 캐시합니다.
    """

    def __init__(self, project_id: str, location: str = "global"):
        self.project_id = project_id
        self.location = location
        self.client = translate_v3.TranslationServiceClient()
        self.parent = f"projects/{project_id}/locations/{location}"

    @lru_cache(maxsize=2048)
    def translate_text(self, text: str, target_language: str, source_language: str | None = None) -> str:
        """일반 텍스트를 목표 언어로 번역합니다.

        주변 구간 반복 재생 시 final 자막이 자주 반복되므로
        이 메서드는 캐시를 사용합니다.
        """
        if not text.strip():
            return ""

        response = self.client.translate_text(
            request={
                "parent": self.parent,
                "contents": [text],
                "mime_type": "text/plain",
                "source_language_code": source_language or "",
                "target_language_code": target_language,
            }
        )
        if not response.translations:
            return ""
        return response.translations[0].translated_text

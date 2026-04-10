from __future__ import annotations
"""타임라인 저장소와 UI에서 사용하는 자막 큐 데이터 모델."""

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class SubtitleCue:
    """타임라인 저장소에 보관되는 단일 자막 큐.

    source_text는 STT 결과이며,
    translated_text는 번역 완료 후 비동기로 채워집니다.
    """

    # 큐 갱신/번역 연결에 사용하는 안정적 식별자.
    cue_id: str
    # SessionManager가 생성한 세션 키.
    session_id: str
    # 원본 미디어 타임라인 기준 큐 시작 시각(초).
    start_time: float
    # 원본 미디어 타임라인 기준 큐 종료 시각(초).
    end_time: float
    # STT 원문.
    source_text: str
    # 번역문(이후 비동기로 채워짐).
    translated_text: Optional[str]
    # 원문 언어 코드.
    source_language: Optional[str]
    # 목표 언어 코드.
    target_language: Optional[str]
    # source_text가 final STT 결과인지 여부.
    is_final: bool
    # STT 제공자의 confidence(선택값).
    confidence: Optional[float]
    # 진단/보존 로직에 사용하는 시각 정보.
    created_monotonic: float
    updated_monotonic: float

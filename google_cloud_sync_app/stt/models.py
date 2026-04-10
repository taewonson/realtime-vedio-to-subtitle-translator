from __future__ import annotations
"""모듈 간 공유되는 정규화 STT 모델 스키마."""

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class STTResult:
    """애플리케이션 전반에서 사용하는 정규화된 스트리밍 인식 결과.

    start_time/end_time은 항상 원본 미디어 타임라인 기준 초 단위입니다.
    Google STT의 경우 가능하면 단어 오프셋에서 계산됩니다.
    """

    # 미디어 타임라인 기준 큐 시작 시각(초).
    start_time: float
    # 미디어 타임라인 기준 큐 종료 시각(초).
    end_time: float
    # 인식된 원문 문장.
    transcript: str
    # Google이 이 결과를 최종(final)로 표시했는지 여부.
    is_final: bool
    # confidence는 보통 final 결과에만 제공됩니다.
    confidence: Optional[float]
    # 내부 정렬/디버깅을 위한 로컬 생성 시각.
    created_monotonic: float
    # en-US 같은 BCP-47 언어 코드.
    language_code: Optional[str] = None

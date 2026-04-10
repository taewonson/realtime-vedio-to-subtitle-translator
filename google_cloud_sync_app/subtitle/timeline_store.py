from __future__ import annotations
"""메모리 기반 자막 타임라인 관리.

원문/번역 큐를 저장하고 현재 브라우저 재생 위치에서
표시해야 할 자막을 계산합니다.
"""

import threading
import time
import uuid
from typing import Optional

from .models import SubtitleCue
from stt.models import STTResult


class SubtitleTimelineStore:
    """자막 큐를 원본 미디어 타임라인과 정렬된 상태로 유지합니다.

    책임:
    - 반복되는 interim/final 결과 중복 제거
    - 동일 구간의 final 문장이 오면 기존 큐 갱신
    - 특정 브라우저 currentTime에서 표시할 큐 반환
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cues: list[SubtitleCue] = []

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().strip().split())

    def clear(self) -> None:
        """모든 큐를 삭제합니다(새 세션 시작 시 사용)."""
        with self._lock:
            self._cues.clear()

    def upsert_from_stt(self, session_id: str, result: STTResult) -> str:
        """STT 출력으로 큐를 삽입/갱신하고 큐 ID를 반환합니다."""
        normalized = self._normalize(result.transcript)
        now = time.monotonic()

        with self._lock:
            # 시간 구간이 거의 같으면 기존 큐를 갱신합니다.
            for cue in reversed(self._cues[-20:]):
                if cue.session_id != session_id:
                    continue
                # 시간/문장이 거의 동일하면 같은 큐로 취급해
                # interim/final이 중복 생성되지 않고 병합되도록 합니다.
                overlap = abs(cue.start_time - result.start_time) <= 1.0 and abs(cue.end_time - result.end_time) <= 1.5
                same_text = self._normalize(cue.source_text) == normalized
                if overlap or same_text:
                    cue.start_time = min(cue.start_time, result.start_time)
                    cue.end_time = max(cue.end_time, result.end_time)
                    cue.source_text = result.transcript
                    cue.source_language = result.language_code
                    cue.is_final = result.is_final or cue.is_final
                    cue.confidence = result.confidence if result.confidence is not None else cue.confidence
                    cue.updated_monotonic = now
                    return cue.cue_id

            cue = SubtitleCue(
                cue_id=str(uuid.uuid4()),
                session_id=session_id,
                start_time=result.start_time,
                end_time=result.end_time,
                source_text=result.transcript,
                translated_text=None,
                source_language=result.language_code,
                target_language=None,
                is_final=result.is_final,
                confidence=result.confidence,
                created_monotonic=now,
                updated_monotonic=now,
            )
            self._cues.append(cue)
            self._cues.sort(key=lambda x: x.start_time)
            return cue.cue_id

    def attach_translation(self, cue_id: str, translated_text: str, target_language: str) -> None:
        """비동기 번역 작업 완료 후 번역문을 큐에 연결합니다."""
        with self._lock:
            for cue in self._cues:
                if cue.cue_id == cue_id:
                    cue.translated_text = translated_text
                    cue.target_language = target_language
                    cue.updated_monotonic = time.monotonic()
                    break

    def get_active_cue(self, session_id: str, current_time: float, hold_seconds: float = 0.6) -> Optional[SubtitleCue]:
        """현재 재생 시점에서 활성 상태인 최신 큐를 반환합니다."""
        with self._lock:
            active = [
                cue
                for cue in self._cues
                if cue.session_id == session_id
                and cue.start_time <= current_time <= cue.end_time + hold_seconds
            ]
            return active[-1] if active else None

    def prune_old(self, session_id: str, min_keep_time: float) -> None:
        """오래된 큐를 제거해 메모리 사용량을 제한합니다."""
        with self._lock:
            self._cues = [
                cue for cue in self._cues
                if cue.session_id != session_id or cue.end_time >= min_keep_time
            ]

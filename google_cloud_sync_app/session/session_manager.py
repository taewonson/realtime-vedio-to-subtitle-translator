from __future__ import annotations
"""세션 및 재시작 판단 정책.

연속된 브라우저 상태 스냅샷을 비교해 오디오/STT 파이프라인을
재시작해야 하는지(새 영상, 새 탭 세션, 강한 seek)를 결정합니다.
"""

import time
from dataclasses import dataclass
from typing import Optional

from browser_sync.models import BrowserState


@dataclass(slots=True)
class SessionDecision:
    """SessionManager.update 결과(재시작 판단 메타데이터)."""
    reason: str
    should_restart_pipeline: bool
    session_key: str


class SessionManager:
    """브라우저 재생 상태를 추적하고 파이프라인 재시작 시점을 판단합니다.

    재시작 조건:
    - 첫 영상 감지
    - URL 변경
    - 강한 seek 감지
    - 확장 프로그램 session_id 변경
    """

    def __init__(self):
        self.current_session_key = ""
        self.current_url = ""
        self.last_browser_time: Optional[float] = None
        self.last_monotonic: Optional[float] = None
        self.last_restart_monotonic = 0.0
        self.restart_cooldown_seconds = 8.0

    @staticmethod
    def build_session_key(state: BrowserState) -> str:
        """가능하면 확장 프로그램 session_id를, 없으면 URL을 사용합니다."""
        return state.session_id or state.url

    def update(self, state: BrowserState) -> SessionDecision:
        """내부 상태를 갱신하고 현재 이벤트의 재시작 판단 결과를 반환합니다."""
        session_key = self.build_session_key(state)
        now = time.monotonic()

        if not self.current_session_key:
            self.current_session_key = session_key
            self.current_url = state.url
            self.last_browser_time = state.current_time
            self.last_monotonic = state.received_monotonic
            self.last_restart_monotonic = now
            return SessionDecision("첫 영상", True, session_key)

        if session_key != self.current_session_key or state.url != self.current_url:
            self.current_session_key = session_key
            self.current_url = state.url
            self.last_browser_time = state.current_time
            self.last_monotonic = state.received_monotonic
            self.last_restart_monotonic = now
            return SessionDecision("새 영상 또는 새 세션", True, session_key)

        should_restart = False
        reason = "유지"
        if self.last_browser_time is not None and self.last_monotonic is not None:
            dt_media = state.current_time - self.last_browser_time
            dt_wall = state.received_monotonic - self.last_monotonic
            # 재생 속도를 반영해 의미 있는 seek/점프를 감지합니다.
            strong_seek = abs(dt_media - dt_wall * max(state.playback_rate, 0.1)) > 5.0 or dt_media < -2.0
            cooldown_ok = (now - self.last_restart_monotonic) >= self.restart_cooldown_seconds
            if strong_seek and cooldown_ok:
                should_restart = True
                reason = "seek 감지"
                self.last_restart_monotonic = now

        self.last_browser_time = state.current_time
        self.last_monotonic = state.received_monotonic
        return SessionDecision(reason, should_restart, self.current_session_key)

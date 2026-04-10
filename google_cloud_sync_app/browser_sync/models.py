from __future__ import annotations
"""브라우저 동기화 이벤트용 데이터 모델."""

from dataclasses import dataclass


@dataclass(slots=True)
class BrowserState:
    """Chrome 확장 프로그램이 보낸 최신 재생 상태.

    session_id:
        확장 프로그램이 만든 탭/영상 단위 식별자.
    url:
        현재 페이지 URL.
    current_time:
        YouTube 플레이어 currentTime(초).
    paused:
        일시정지 여부.
    title:
        현재 문서 제목.
    playback_rate:
        YouTube 재생 속도(예: 1.25x, 1.5x).
    received_monotonic:
        Python 앱에 도착한 시점의 로컬 monotonic 시각.
    """

    # 확장 프로그램이 생성한 세션 식별자(탭/영상 범위).
    session_id: str
    # 이벤트가 수집된 전체 브라우저 URL.
    url: str
    # 플레이어 currentTime(초).
    current_time: float
    # 플레이어 일시정지 상태.
    paused: bool
    # 이벤트 시점 문서 제목.
    title: str
    # 재생 속도(예: 1.0, 1.25, 1.5).
    playback_rate: float
    # 미디어 시간 델타와 비교해 seek를 감지할 때 사용하는 로컬 수신 시각.
    received_monotonic: float

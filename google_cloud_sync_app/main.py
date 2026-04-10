from __future__ import annotations
"""애플리케이션 진입점 및 오케스트레이션 레이어.

이 모듈은 아래의 독립 서비스들을 연결합니다.
- 브라우저 동기화 HTTP 수신
- YouTube 오디오 URL 해석 및 ffmpeg PCM 변환
- Google Speech 스트리밍 STT
- Google Translate v3
- 자막 타임라인 저장소
- Tkinter UI 렌더링

대부분의 비즈니스 로직은 개별 모듈에 위임하고,
이 파일은 수명주기와 데이터 흐름 조정에 집중합니다.
"""

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import tkinter as tk
from tkinter import ttk

from browser_sync.models import BrowserState
from browser_sync.server import BrowserSyncServer
from media.ffmpeg_streamer import FFmpegPCMStreamer
from media.youtube_audio_source import YouTubeAudioResolver
from env_config import load_app_config
from session.session_manager import SessionManager
from stt.google_speech_adapter import GoogleSpeechStreamingAdapter
from subtitle.timeline_store import SubtitleTimelineStore
from translate.google_translate_adapter import GoogleTranslateAdapter
from ui.tkinter_view import MainWindow

SYNC_SERVER_HOST = "127.0.0.1"
SYNC_SERVER_PORT = 8765
BROWSER_STALE_SECONDS = 2.0
MAX_BUFFER_SECONDS = 180.0

APP_CONFIG = load_app_config()


class AppCoordinator:
    """브라우저 동기화, 오디오 스트리밍, STT, 번역, UI를 총괄하는 상위 코디네이터.

    이 객체는 의도적으로 오케스트레이션만 담당합니다.
    서비스별 로직은 개별 모듈에 분리되어 있어,
    향후 Google Cloud를 다른 제공자로 교체할 때 전체 프로그램을
    다시 작성하지 않아도 됩니다.
    """

    def __init__(self, root: tk.Tk):
        # 클라우드 프로젝트 정보가 없으면 즉시 실패 처리합니다.
        # 이 값이 없으면 번역 클라이언트 경로를 만들 수 없습니다.
        if not APP_CONFIG.google_project_id:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT 환경 변수가 비어 있습니다.\n"
                "google_cloud_sync_app/.env 파일 또는 시스템 환경변수에 값을 설정하세요."
            )

        self.root = root
        self.ui = MainWindow(root)
        self.ui.set_target_language(APP_CONFIG.target_language_code)

        self.browser_state_queue: queue.Queue[BrowserState] = queue.Queue()
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.sync_server = BrowserSyncServer(
            host=SYNC_SERVER_HOST,
            port=SYNC_SERVER_PORT,
            state_queue=self.browser_state_queue,
            status_queue=self.status_queue,
        )

        self.session_manager = SessionManager()
        self.audio_resolver = YouTubeAudioResolver(timeout_seconds=30)
        self.ffmpeg_streamer = FFmpegPCMStreamer(sample_rate=16000, channels=1, bytes_per_sample=2, chunk_seconds=0.4)
        self.stt_adapter = GoogleSpeechStreamingAdapter(
            sample_rate_hz=16000,
            language_code=APP_CONFIG.source_language_code,
            interim_results=True,
            model="default",
        )
        self.translate_adapter = GoogleTranslateAdapter(
            project_id=APP_CONFIG.google_project_id,
            location=APP_CONFIG.google_translate_location,
        )
        self.timeline_store = SubtitleTimelineStore()
        self.translation_executor = ThreadPoolExecutor(max_workers=4)

        self.current_browser_state: Optional[BrowserState] = None
        self.current_session_key: str = ""
        self.current_video_url: str = ""
        self.pipeline_thread: Optional[threading.Thread] = None
        self.pipeline_stop_event = threading.Event()

        # 로컬 동기화 서버를 먼저 시작해 UI가 뜬 직후부터
        # 확장 프로그램 payload를 바로 수신할 수 있게 합니다.
        self.sync_server.start()
        self.status_queue.put("Google Cloud 기반 자막 파이프라인 준비 완료")
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _restart_pipeline(self, state: BrowserState) -> None:
        """브라우저 현재 재생 시점에서 스트리밍 파이프라인을 재시작합니다."""
        self._stop_pipeline()
        self.timeline_store.clear()
        self.current_video_url = state.url
        self.current_session_key = self.session_manager.build_session_key(state)
        self.pipeline_stop_event.clear()

        self.pipeline_thread = threading.Thread(
            target=self._pipeline_main,
            args=(state.url, state.current_time),
            daemon=True,
        )
        self.pipeline_thread.start()
        self.status_queue.put(f"파이프라인 재시작: {state.title or state.url}")

    def _stop_pipeline(self) -> None:
        """실행 중인 스트리밍 작업을 종료시키고 현재 STT 스트림을 닫습니다."""
        self.pipeline_stop_event.set()
        self.stt_adapter.close_stream()

    def _pipeline_main(self, video_url: str, start_offset_seconds: float) -> None:
        """워커 스레드에서 오디오 -> STT -> 타임라인 갱신 루프를 실행합니다."""
        try:
            # 1) 일반 YouTube 페이지 URL에서 실제 재생 가능한 미디어 URL을 해석합니다.
            direct_audio_url = self.audio_resolver.resolve(video_url)
            self.status_queue.put("오디오 스트림 URL 확보 완료")

            # 2) Speech 스트리밍 세션을 열고 PCM 청크를 지속적으로 공급합니다.
            self.stt_adapter.start_stream()
            for pcm_chunk in self.ffmpeg_streamer.iter_pcm_chunks(direct_audio_url, start_offset_seconds=start_offset_seconds):
                # 사용자가 영상/세션을 바꿨거나 종료 요청이 들어오면 빠르게 중단합니다.
                if self.pipeline_stop_event.is_set() or video_url != self.current_video_url:
                    break

                self.stt_adapter.push_audio(pcm_chunk)

            self.stt_adapter.close_stream()
        except Exception as exc:
            self.status_queue.put(f"파이프라인 오류: {exc}")

    def _translate_and_attach(self, cue_id: str, source_text: str) -> None:
        """확정된 원문을 번역하고 해당 자막 큐에 번역문을 연결합니다."""
        try:
            translated = self.translate_adapter.translate_text(
                text=source_text,
                source_language=APP_CONFIG.source_language_code.split("-")[0],
                target_language=APP_CONFIG.target_language_code,
            )
            self.timeline_store.attach_translation(cue_id, translated, APP_CONFIG.target_language_code)
        except Exception as exc:
            self.status_queue.put(f"번역 오류: {exc}")

    def _handle_browser_state(self, state: BrowserState) -> None:
        """UI를 갱신하고 파이프라인 재시작 필요 여부를 판단합니다."""
        self.current_browser_state = state
        self.ui.set_video(state.title)
        self.ui.set_browser_time(self.ui._fmt(state.current_time))

        decision = self.session_manager.update(state)
        self.current_session_key = decision.session_key

        if decision.should_restart_pipeline:
            self._restart_pipeline(state)

    def _poll(self) -> None:
        """Tkinter 메인 루프 tick: 큐 소비, 자막 갱신, 오래된 기록 정리를 수행합니다."""
        try:
            while True:
                status = self.status_queue.get_nowait()
                self.ui.set_status(status)
        except queue.Empty:
            pass

        try:
            while True:
                state = self.browser_state_queue.get_nowait()
                self._handle_browser_state(state)
        except queue.Empty:
            pass

        # STT 결과/상태 큐 소비는 UI 스레드의 _poll에서만 수행해
        # 중복 소비(race condition) 가능성을 제거합니다.
        for stt_status in self.stt_adapter.poll_statuses():
            self.ui.set_status(stt_status)

        for stt_result in self.stt_adapter.poll_results():
            cue_id = self.timeline_store.upsert_from_stt(self.current_session_key, stt_result)
            if stt_result.is_final:
                self.translation_executor.submit(self._translate_and_attach, cue_id, stt_result.transcript)

        if self.current_browser_state is not None:
            # 확장 프로그램 상태 전송이 끊기면 경고를 띄워
            # 운영자가 브라우저 연결 상태를 빠르게 확인할 수 있게 합니다.
            if time.monotonic() - self.current_browser_state.received_monotonic > BROWSER_STALE_SECONDS:
                self.ui.set_status("브라우저 상태 업데이트가 끊겼습니다. 확장 프로그램 연결을 확인하세요.")

            current_time = self.current_browser_state.current_time
            cue = self.timeline_store.get_active_cue(self.current_session_key, current_time)
            self.ui.show_cue(cue)
            # 긴 영상에서도 메모리 사용량이 과도해지지 않도록 정리합니다.
            self.timeline_store.prune_old(self.current_session_key, current_time - MAX_BUFFER_SECONDS)

        self.root.after(100, self._poll)

    def _on_close(self) -> None:
        """창 닫기 이벤트 시 안전하게 종료 처리합니다."""
        self._stop_pipeline()
        self.sync_server.stop()
        self.translation_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> None:
    """Tkinter 앱과 코디네이터를 초기화하고 실행합니다."""
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    AppCoordinator(root)
    root.mainloop()


if __name__ == "__main__":
    main()

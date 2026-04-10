from __future__ import annotations
"""Google Speech 스트리밍 어댑터.

ffmpeg에서 push되는 PCM 입력을 Google 양방향 gRPC 스트리밍 API를 통해
정규화된 STTResult 출력으로 변환합니다.
"""

import queue
import threading
import time
from typing import Iterable, Optional

from google.cloud import speech_v1 as speech

from .models import STTResult


class GoogleSpeechStreamingAdapter:
    """PCM 청크와 Google Cloud 스트리밍 인식을 연결하는 브리지.

    주요 설계 포인트:
    - streaming_recognize는 블로킹 양방향 gRPC 호출이므로
      어댑터가 워커 스레드를 직접 소유합니다.
    - 애플리케이션 나머지 부분은 PCM 바이트를 push하고
      정규화된 STTResult를 poll만 하도록 단순화합니다.
    - 최종 결과의 브라우저 currentTime 정렬 정확도를 높이기 위해
      단어 시간 오프셋을 활성화합니다.
    """

    def __init__(
        self,
        sample_rate_hz: int = 16000,
        language_code: str = "en-US",
        interim_results: bool = True,
        model: str = "default",
        enable_automatic_punctuation: bool = True,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.language_code = language_code
        self.interim_results = interim_results
        self.model = model
        self.enable_automatic_punctuation = enable_automatic_punctuation

        self.client = speech.SpeechClient()
        self.audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self.result_queue: queue.Queue[STTResult] = queue.Queue()
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.stream_open = False

        # 응답에 단어 단위 시간이 없을 때만 사용하는 fallback 값입니다.
        self.last_final_end_time = 0.0

    def start_stream(self) -> None:
        """파이프라인 생명주기마다 스트리밍 워커 스레드를 1회 엽니다."""
        if self.worker_thread and self.worker_thread.is_alive():
            return

        self.stop_event.clear()
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.last_final_end_time = 0.0
        self.worker_thread = threading.Thread(target=self._run_streaming, daemon=True)
        self.worker_thread.start()
        self.stream_open = True

    def push_audio(self, pcm_chunk: bytes) -> None:
        """gRPC 요청 생성기가 읽을 raw PCM 청크를 큐에 적재합니다."""
        if self.stream_open and not self.stop_event.is_set():
            self.audio_queue.put(pcm_chunk)

    def poll_results(self) -> list[STTResult]:
        """현재 누적된 STT 결과를 블로킹 없이 모두 꺼냅니다."""
        results: list[STTResult] = []
        while True:
            try:
                results.append(self.result_queue.get_nowait())
            except queue.Empty:
                break
        return results

    def poll_statuses(self) -> list[str]:
        """워커 스레드가 생성한 상태 메시지를 모두 꺼냅니다."""
        statuses: list[str] = []
        while True:
            try:
                statuses.append(self.status_queue.get_nowait())
            except queue.Empty:
                break
        return statuses

    def close_stream(self) -> None:
        """요청 생성기와 워커 루프에 종료 신호를 보냅니다."""
        if not self.stream_open:
            return
        self.stop_event.set()
        self.audio_queue.put(None)
        self.stream_open = False

    def _request_generator(self) -> Iterable[speech.StreamingRecognizeRequest]:
        """설정 프레임을 먼저 보내고 이후 오디오 프레임을 순차 전송합니다."""
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.sample_rate_hz,
            language_code=self.language_code,
            enable_automatic_punctuation=self.enable_automatic_punctuation,
            enable_word_time_offsets=True,
            model=self.model,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=self.interim_results,
            single_utterance=False,
        )
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)

        while not self.stop_event.is_set():
            chunk = self.audio_queue.get()
            if chunk is None:
                break
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    @staticmethod
    def _duration_to_seconds(duration) -> float:
        if duration is None:
            return 0.0
        return float(duration.seconds) + float(duration.nanos) / 1_000_000_000.0

    def _normalize_result(self, result) -> Optional[STTResult]:
        """원시 API 결과를 앱 내부 STTResult 스키마로 변환합니다."""
        if not result.alternatives:
            return None
        alt = result.alternatives[0]
        transcript = (alt.transcript or "").strip()
        if not transcript:
            return None

        if alt.words:
            # 더 안정적인 큐 정렬을 위해 단어 단위 오프셋을 우선 사용합니다.
            start_time = self._duration_to_seconds(alt.words[0].start_time)
            end_time = self._duration_to_seconds(alt.words[-1].end_time)
        else:
            # 단어 오프셋이 없는 경우의 fallback 처리입니다.
            end_time = self._duration_to_seconds(getattr(result, "result_end_time", None))
            if result.is_final:
                start_time = self.last_final_end_time
            else:
                start_time = max(0.0, end_time - 2.0)

        if result.is_final:
            self.last_final_end_time = max(self.last_final_end_time, end_time)

        confidence = None
        if result.is_final and getattr(alt, "confidence", 0.0):
            confidence = float(alt.confidence)

        return STTResult(
            start_time=max(0.0, start_time),
            end_time=max(0.0, end_time),
            transcript=transcript,
            is_final=bool(result.is_final),
            confidence=confidence,
            created_monotonic=time.monotonic(),
            language_code=self.language_code,
        )

    def _run_streaming(self) -> None:
        """스트리밍 응답을 소비하고 정규화하는 백그라운드 워커입니다."""
        try:
            responses = self.client.streaming_recognize(requests=self._request_generator())
            self.status_queue.put("Google Speech 스트리밍 연결됨")
            for response in responses:
                if self.stop_event.is_set():
                    break
                for result in response.results:
                    normalized = self._normalize_result(result)
                    if normalized is not None:
                        self.result_queue.put(normalized)
        except Exception as exc:
            self.status_queue.put(f"Google Speech 스트리밍 오류: {exc}")
        finally:
            self.stream_open = False

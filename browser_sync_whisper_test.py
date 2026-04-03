# -*- coding: utf-8 -*-
"""
브라우저-동기화 기반 Whisper 자막 시험용 프로그램
================================================

목표
----
- 유튜브 URL을 수동 입력하지 않고, 브라우저에서 재생 중인 영상의 상태를 받아
  Python 프로그램이 그 시간축에 맞춰 자막을 표시한다.
- 외부 STT API 없이 로컬 Whisper(openai-whisper)로 동작한다.
- Raspberry Pi 없이 PC 창에서 자막을 표시한다.
- "잘 작동하면 이 구조를 기반으로 확장"할 수 있도록 코드 구조를 분리했다.

핵심 구조
---------
[브라우저]
  currentTime / paused / url / title
      ↓ HTTP POST (localhost)
[Python 앱]
  브라우저 상태 수신
      ↓
  yt-dlp + ffmpeg 로 같은 영상의 오디오 스트림 별도 추출
      ↓
  Whisper 전사
      ↓
  subtitle event 생성 (start_time, end_time, text)
      ↓
  브라우저 currentTime 기준으로 자막 표시

중요한 한계
-----------
- 이 코드는 "브라우저 재생 시간과 동기화"를 검증하는 시험용이다.
- 브라우저 오디오를 직접 캡처하지 않는다.
- 유튜브 플레이어가 광고/버퍼링/강제 재로드 등 특수 상황일 때 오차가 생길 수 있다.
- 브라우저 쪽 코드(간단한 확장 / 콘솔 스니펫 / 북마클릿 중 하나)는 별도 필요하다.

필수 설치 예시
--------------
pip install numpy whisper
pip install yt-dlp
ffmpeg 설치 필요 (PATH 등록)

실행
----
python browser_sync_whisper_test.py

브라우저 쪽 스니펫은 같은 폴더의 browser_sync_snippet.js 참고
"""

import json
import queue
import re
import subprocess
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

import whisper


SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2

CHUNK_SECONDS = 4.0
OVERLAP_SECONDS = 1.0

CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)
OVERLAP_SAMPLES = int(SAMPLE_RATE * OVERLAP_SECONDS)
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE

MODEL_NAME = "base"
LANGUAGE_HINT = None

MIN_TEXT_LENGTH = 2
DUPLICATE_TIME_WINDOW = 1.5
SUBTITLE_HOLD_SECONDS = 1.2
LATE_ACCEPT_SECONDS = 10.0
MAX_BUFFER_SECONDS = 180.0
SYNC_OFFSET_SECONDS = 0.15

SYNC_SERVER_HOST = "127.0.0.1"
SYNC_SERVER_PORT = 8765
BROWSER_STALE_SECONDS = 2.0
YTDLP_TIMEOUT = 30


def _first_http_url(text: str) -> str:
    match = re.search(r"https?://\S+", text)
    return match.group(0).strip() if match else ""


def _extract_title_with_python_api(video_url: str) -> str:
    try:
        import yt_dlp
    except Exception:
        return ""

    option_sets = [
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        },
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "ba/b",
        },
    ]

    for opts in option_sets:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception:
            continue

        if isinstance(info, dict):
            title = info.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()

    return ""


def _first_stream_url(text: str) -> str:
    for raw in re.findall(r"https?://\S+", text):
        url = raw.strip().rstrip("\"').,;]")
        low = url.lower()

        if "youtube.com" in low and ("watch?v=" in low or "youtu.be/" in low):
            continue
        if "github.com/yt-dlp/yt-dlp/wiki" in low:
            continue

        if (
            "googlevideo.com" in low
            or "videoplayback" in low
            or ".m3u8" in low
            or ".mpd" in low
            or "manifest" in low
            or "mime=" in low
            or "source=youtube" in low
        ):
            return url

    return ""


def _extract_stream_url_with_python_api(video_url: str) -> str:
    try:
        import yt_dlp
    except Exception:
        return ""

    option_sets = [
        {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "format": "ba/b"},
        {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "format": "b"},
    ]

    for opts in option_sets:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception:
            continue

        if isinstance(info, dict) and info.get("entries"):
            info = next((entry for entry in info.get("entries", []) if entry), info)

        if not isinstance(info, dict):
            continue

        candidates = []
        for key in ("url", "manifest_url", "hls_url", "dash_url"):
            value = info.get(key)
            if isinstance(value, str) and value.startswith("http"):
                candidates.append(value)

        for fmt in info.get("requested_formats", []) or []:
            value = fmt.get("url") if isinstance(fmt, dict) else None
            if isinstance(value, str) and value.startswith("http"):
                candidates.append(value)

        for candidate in candidates:
            stream_url = _first_stream_url(candidate)
            if stream_url:
                return stream_url

    return ""


def _resolve_ffmpeg_executable() -> str:
    candidates = [
        shutil.which("ffmpeg"),
        str(Path.home() / r"AppData\Local\Microsoft\WindowsApps\ffmpeg.exe"),
        str(Path.home() / r"AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    raise RuntimeError(
        "ffmpeg 실행 파일을 찾을 수 없습니다.\n"
        "PowerShell/VS Code 터미널을 재시작한 뒤 다시 시도하거나, ffmpeg.exe 경로를 PATH에 추가하세요."
    )


@dataclass
class SubtitleEvent:
    start_time: float
    end_time: float
    text: str
    created_monotonic: float


@dataclass
class BrowserState:
    url: str
    current_time: float
    paused: bool
    title: str
    received_monotonic: float


class BrowserSyncServer:
    def __init__(self, host: str, port: int, state_queue: queue.Queue, status_queue: queue.Queue):
        self.host = host
        self.port = port
        self.state_queue = state_queue
        self.status_queue = status_queue
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, code: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.end_headers()

            def do_GET(self):
                if self.path == "/health":
                    self._send_json(200, {"ok": True})
                    return

                self._send_json(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                if self.path != "/sync":
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    payload = json.loads(raw.decode("utf-8"))

                    state = BrowserState(
                        url=str(payload.get("url", "")).strip(),
                        current_time=float(payload.get("currentTime", 0.0)),
                        paused=bool(payload.get("paused", False)),
                        title=str(payload.get("title", "")).strip(),
                        received_monotonic=time.monotonic(),
                    )
                    outer.state_queue.put(state)
                    self._send_json(200, {"ok": True})
                except Exception as e:
                    outer.status_queue.put(f"브라우저 상태 수신 오류: {e}")
                    self._send_json(400, {"ok": False, "error": str(e)})

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.status_queue.put(f"브라우저 동기화 서버 시작: http://{self.host}:{self.port}/sync")

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None


class BrowserSyncWhisperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("브라우저 동기화 Whisper 시험용")
        self.root.geometry("1100x420")

        self.model = None

        # 스레드 간 통신 큐: 상태/자막/브라우저 이벤트를 메인 UI 스레드에서 일괄 처리한다.
        self.status_queue: queue.Queue = queue.Queue()
        self.subtitle_queue: queue.Queue = queue.Queue()
        self.browser_state_queue: queue.Queue = queue.Queue()

        self.sync_server = BrowserSyncServer(
            SYNC_SERVER_HOST,
            SYNC_SERVER_PORT,
            self.browser_state_queue,
            self.status_queue,
        )

        self.worker_thread: Optional[threading.Thread] = None
        self.worker_stop_flag = threading.Event()
        self.current_stream_url: str = ""
        self.current_video_url: str = ""
        self.current_video_title: str = ""
        self.transcribe_session_id = 0
        self.current_stream_start_offset: float = 0.0

        # 브라우저에서 주기적으로 전달되는 재생 상태 스냅샷
        self.browser_current_time: float = 0.0
        self.browser_paused: bool = True
        self.browser_last_update_monotonic: Optional[float] = None
        self.browser_url: str = ""
        self.browser_title: str = ""

        # 전사 결과 버퍼와 화면 표시 상태(이전 줄/현재 줄)
        self.subtitle_buffer = []
        self.last_displayed_text = ""
        self.last_displayed_start = -999.0
        self.prev_display_text = ""
        self.current_display_text = ""
        self.last_subtitle_update_monotonic: Optional[float] = None

        self.last_browser_current_time: Optional[float] = None
        self.last_browser_update_monotonic: Optional[float] = None

        self.latency_samples = []

        self._build_ui()
        self.sync_server.start()
        self._load_model_async()
        self.root.after(100, self._poll_queues)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="브라우저 연동 안내: browser_sync_snippet.js 를 콘솔에 실행하면 아래 상태가 자동으로 갱신됩니다.",
        ).pack(anchor="w")

        self.status_var = tk.StringVar(value="초기화 중...")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))

        self.video_var = tk.StringVar(value="영상: 없음")
        ttk.Label(top, textvariable=self.video_var, font=("Arial", 10, "bold")).pack(anchor="w", pady=(6, 0))

        info_frame = ttk.Frame(top)
        info_frame.pack(fill="x", pady=(8, 0))

        self.browser_time_var = tk.StringVar(value="브라우저 시간: 00:00.00")
        self.model_var = tk.StringVar(value=f"모델: {MODEL_NAME} (로딩 중)")
        self.latency_var = tk.StringVar(value="평균 표시 지연: 측정 전")

        ttk.Label(info_frame, textvariable=self.browser_time_var).pack(side="left")
        ttk.Label(info_frame, textvariable=self.model_var).pack(side="left", padx=(20, 0))
        ttk.Label(info_frame, textvariable=self.latency_var).pack(side="left", padx=(20, 0))

        control_frame = ttk.Frame(top)
        control_frame.pack(fill="x", pady=(10, 0))

        self.clear_btn = ttk.Button(control_frame, text="자막 버퍼 비우기", command=self.clear_subtitles)
        self.clear_btn.pack(side="left")

        self.save_log_btn = ttk.Button(control_frame, text="지연 로그 저장", command=self.save_latency_log)
        self.save_log_btn.pack(side="left", padx=(8, 0))

        body = ttk.Frame(self.root, padding=(10, 10, 10, 10))
        body.pack(fill="both", expand=True)

        self.time_var = tk.StringVar(value="00:00.00 ~ 00:00.00")
        ttk.Label(body, textvariable=self.time_var, font=("Arial", 12)).pack(anchor="center", pady=(0, 8))

        self.subtitle_var = tk.StringVar(value="브라우저에서 유튜브 재생 후 동기화 스니펫을 실행하세요.")
        self.subtitle_label = ttk.Label(
            body,
            textvariable=self.subtitle_var,
            anchor="center",
            justify="center",
            font=("Arial", 23),
            wraplength=980,
        )
        self.subtitle_label.pack(fill="both", expand=True)

    def _load_model_async(self):
        def load():
            try:
                self.status_queue.put("Whisper 모델 로딩 시작...")
                self.model = whisper.load_model(MODEL_NAME)
                self.status_queue.put(f"Whisper 모델 로딩 완료: {MODEL_NAME}")
                self.root.after(0, lambda: self.model_var.set(f"모델: {MODEL_NAME} (준비 완료)"))
            except Exception as e:
                self.status_queue.put(f"Whisper 모델 로딩 실패: {e}")
                self.root.after(0, lambda: self.model_var.set(f"모델 로딩 실패: {e}"))

        threading.Thread(target=load, daemon=True).start()

    def _handle_browser_state(self, state: BrowserState):
        # 브라우저에서 들어온 상태를 내부 기준 시간으로 반영하고,
        # URL 변경(새 영상) 시 오디오 전사 워커를 재시작한다.
        prev_url = self.browser_url
        self.browser_url = state.url
        self.browser_title = state.title
        self.browser_current_time = state.current_time
        self.browser_paused = state.paused
        self.browser_last_update_monotonic = state.received_monotonic

        self.browser_time_var.set(f"브라우저 시간: {self._fmt(self.browser_current_time)}")
        title_text = self.browser_title if self.browser_title else "(제목 없음)"
        self.video_var.set(f"영상: {title_text}")

        if self.last_browser_current_time is not None and self.last_browser_update_monotonic is not None:
            dt_wall = state.received_monotonic - self.last_browser_update_monotonic
            dt_media = state.current_time - self.last_browser_current_time
            seeked = (not state.paused) and abs(dt_media - dt_wall) > 2.0
            if seeked:
                self.status_queue.put("브라우저 seek 감지 - 시간축 재정렬")
                if state.url and state.url == prev_url and self.model is not None:
                    self._restart_worker_for_video(state.url, start_offset_seconds=state.current_time)

        self.last_browser_current_time = state.current_time
        self.last_browser_update_monotonic = state.received_monotonic

        if state.url and state.url != prev_url:
            if self.model is None:
                self.status_queue.put("새 영상 감지됨. 모델 로딩 완료 후 시작됩니다.")
            else:
                self.status_queue.put("새 영상 감지됨. 오디오 스트림을 다시 연결합니다.")
                self._restart_worker_for_video(state.url, start_offset_seconds=state.current_time)

    def _restart_worker_for_video(self, video_url: str, start_offset_seconds: float = 0.0):
        self._stop_worker()

        self.current_video_url = video_url
        self.current_stream_url = ""
        self.current_stream_start_offset = max(0.0, float(start_offset_seconds))
        self.transcribe_session_id += 1
        self.last_displayed_text = ""
        self.last_displayed_start = -999.0
        self.prev_display_text = ""
        self.current_display_text = ""
        self.subtitle_buffer.clear()

        self.worker_stop_flag.clear()
        self.worker_thread = threading.Thread(
            target=self._worker_main,
            args=(video_url, self.transcribe_session_id, self.current_stream_start_offset),
            daemon=True,
        )
        self.worker_thread.start()

    def _stop_worker(self):
        self.worker_stop_flag.set()
        self.current_stream_url = ""

    def _worker_main(self, video_url: str, session_id: int, start_offset_seconds: float):
        try:
            direct_url = self._get_direct_audio_url(video_url)
            self.current_stream_url = direct_url
            self.status_queue.put(f"오디오 스트림 URL 확보 완료 (시작 위치 {start_offset_seconds:.1f}s)")
            self._stream_and_transcribe(direct_url, session_id, start_offset_seconds)
        except Exception as e:
            self.status_queue.put(f"오디오 워커 오류: {e}")

    def _get_direct_audio_url(self, video_url: str) -> str:
        api_url = _extract_stream_url_with_python_api(video_url)
        if api_url:
            return api_url

        strategies = [
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "url", "-f", "ba/b", video_url],
            [sys.executable, "-m", "yt_dlp", "-f", "ba/b", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "-f", "ba/b", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "-f", "b", "-g", video_url],
        ]

        errors = []
        for i, cmd in enumerate(strategies, 1):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=YTDLP_TIMEOUT)

                direct_url = _first_http_url(result.stdout)
                if direct_url:
                    return direct_url

                direct_url = _first_http_url(result.stderr)
                if direct_url:
                    return direct_url

                if result.returncode != 0:
                    stderr = result.stderr.strip()[:150]
                    errors.append(f"시도 {i} 실패 (RC={result.returncode}): {stderr}")
                else:
                    errors.append(f"시도 {i}: URL 출력 없음")
                    
            except subprocess.TimeoutExpired:
                errors.append(f"시도 {i}: 시간 초과")
            except FileNotFoundError:
                errors.append(f"시도 {i}: 파일 미발견")
            except Exception as e:
                errors.append(f"시도 {i}: {str(e)[:80]}")

        error_msg = "\n- ".join(errors) if errors else "알 수 없는 오류"

        api_fallback_url = _extract_stream_url_with_python_api(video_url)
        if api_fallback_url:
            return api_fallback_url

        raise RuntimeError(
            f"yt-dlp를 사용하여 YouTube 오디오 URL을 추출할 수 없습니다.\n\n"
            f"시도 결과:\n- {error_msg}\n\n"
            f"해결 방법:\n"
            f"1. yt-dlp 최신 버전 설치: pip install -U yt-dlp\n"
            f"2. URL이 유효한지 확인: {video_url}\n"
            f"3. YouTube 영상이 비공개/삭제/지역제한이 아닌지 확인\n"
            f"4. 선택사항: Node.js 설치 시 더 많은 형식 지원"
        )

    def _stream_and_transcribe(self, audio_url: str, session_id: int, start_offset_seconds: float):
        ffmpeg_executable = _resolve_ffmpeg_executable()
        ffmpeg_cmd = [
            ffmpeg_executable,
            "-loglevel", "error",
            "-re",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-ss", str(max(0.0, start_offset_seconds)),
            "-i", audio_url,
            "-vn",
            "-ac", str(CHANNELS),
            "-ar", str(SAMPLE_RATE),
            "-f", "s16le",
            "pipe:1",
        ]

        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if process.stdout is None:
            raise RuntimeError("ffmpeg stdout 파이프를 열지 못했습니다.")

        overlap_audio = np.zeros(0, dtype=np.float32)
        processed_audio_seconds = 0.0

        # ffmpeg에서 실시간 PCM 오디오를 청크 단위로 읽고,
        # Whisper 전사 결과를 "절대 시간 자막 이벤트"로 변환해 큐에 넣는다.
        while not self.worker_stop_flag.is_set():
            if session_id != self.transcribe_session_id:
                break

            raw = self._read_exact(process.stdout, CHUNK_BYTES)
            if not raw:
                break

            current_chunk = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            audio_for_stt = np.concatenate([overlap_audio, current_chunk]) if overlap_audio.size > 0 else current_chunk

            chunk_base_time = max(0.0, processed_audio_seconds - OVERLAP_SECONDS)
            self.status_queue.put(f"전사 중... 오디오 기준 {chunk_base_time:.2f}s")

            result = self.model.transcribe(
                audio_for_stt,
                fp16=False,
                language=LANGUAGE_HINT,
                initial_prompt=self.browser_title or None,
                verbose=False,
                condition_on_previous_text=True,
                beam_size=5,
                temperature=0,
                no_speech_threshold=0.55,
                logprob_threshold=-1.0,
                compression_ratio_threshold=2.4,
            )

            for seg in result.get("segments", []):
                start = float(seg["start"]) + chunk_base_time
                end = float(seg["end"]) + chunk_base_time
                text = seg["text"].strip()

                if len(text) < MIN_TEXT_LENGTH:
                    continue

                if session_id != self.transcribe_session_id:
                    break

                if (
                    text == self.last_displayed_text
                    and abs(start - self.last_displayed_start) < DUPLICATE_TIME_WINDOW
                ):
                    continue

                self.subtitle_queue.put(
                    SubtitleEvent(
                        start_time=start,
                        end_time=end,
                        text=text,
                        created_monotonic=time.monotonic(),
                    )
                )

            overlap_audio = current_chunk[-OVERLAP_SAMPLES:].copy() if OVERLAP_SAMPLES > 0 else np.zeros(0, dtype=np.float32)
            processed_audio_seconds += len(current_chunk) / SAMPLE_RATE

        try:
            process.kill()
        except Exception:
            pass

    @staticmethod
    def _read_exact(pipe, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _poll_queues(self):
        # UI 루프: 워커/서버 스레드에서 쌓아둔 이벤트를 짧은 주기로 반영한다.
        try:
            while True:
                status = self.status_queue.get_nowait()
                self.status_var.set(status)
        except queue.Empty:
            pass

        try:
            while True:
                state = self.browser_state_queue.get_nowait()
                self._handle_browser_state(state)
        except queue.Empty:
            pass

        try:
            while True:
                self.subtitle_buffer.append(self.subtitle_queue.get_nowait())
        except queue.Empty:
            pass

        self._merge_subtitle_buffer()
        self._update_display_from_browser_time()
        self.root.after(100, self._poll_queues)

    def _update_display_from_browser_time(self):
        # 핵심 동기화 로직:
        # 브라우저 currentTime을 기준으로 "지금 보여야 할" 자막을 버퍼에서 선택한다.
        if self.browser_last_update_monotonic is not None:
            age = time.monotonic() - self.browser_last_update_monotonic
            if age > BROWSER_STALE_SECONDS:
                self.status_var.set("브라우저 상태 업데이트가 끊겼습니다. 브라우저 스니펫/확장을 확인하세요.")

        current_play_time = self.browser_current_time
        self.subtitle_buffer.sort(key=lambda x: x.start_time)

        active_events = [
            e for e in self.subtitle_buffer
            if e.start_time <= current_play_time + SYNC_OFFSET_SECONDS <= e.end_time + SUBTITLE_HOLD_SECONDS
        ]
        selected = active_events[-1] if active_events else None

        if selected is None:
            # 전사 지연으로 약간 늦게 도착한 자막은 짧은 윈도우에서 보정 표시
            late_candidates = [
                e for e in self.subtitle_buffer
                if e.end_time < current_play_time and (current_play_time - e.end_time) <= LATE_ACCEPT_SECONDS
            ]
            if late_candidates:
                selected = late_candidates[-1]

        if selected is not None:
            if selected.text != self.current_display_text:
                self.prev_display_text = self.current_display_text
                self.current_display_text = selected.text

                approx_latency = time.monotonic() - selected.created_monotonic
                self.latency_samples.append(approx_latency)
                if len(self.latency_samples) > 200:
                    self.latency_samples = self.latency_samples[-200:]
                avg_latency = sum(self.latency_samples) / len(self.latency_samples)
                self.latency_var.set(f"평균 표시 지연(근사): {avg_latency:.2f}s")
                self.last_subtitle_update_monotonic = time.monotonic()

            top_line = self.prev_display_text if self.prev_display_text else " "
            self.subtitle_var.set(f"{top_line}\n{self.current_display_text}")
            self.time_var.set(f"{self._fmt(selected.start_time)} ~ {self._fmt(selected.end_time)}")
            self.last_displayed_text = selected.text
            self.last_displayed_start = selected.start_time
        else:
            if self.last_subtitle_update_monotonic is not None:
                hold_age = time.monotonic() - self.last_subtitle_update_monotonic
                if hold_age <= SUBTITLE_HOLD_SECONDS:
                    return

            self.prev_display_text = self.current_display_text
            self.current_display_text = ""
            self.subtitle_var.set("\n")
            self.time_var.set("00:00.00 ~ 00:00.00")

        min_keep_time = current_play_time - MAX_BUFFER_SECONDS
        self.subtitle_buffer = [e for e in self.subtitle_buffer if e.end_time >= min_keep_time]

    @staticmethod
    def _normalize_text(text: str) -> str:
        t = re.sub(r"\s+", " ", text.strip().lower())
        return re.sub(r"[^\w\s가-힣]", "", t)

    def _is_overlapped_sentence(self, prev_text: str, curr_text: str) -> bool:
        a = self._normalize_text(prev_text)
        b = self._normalize_text(curr_text)
        if not a or not b:
            return False
        if a in b or b in a:
            return True

        max_check = min(24, len(a), len(b))
        for n in range(max_check, 5, -1):
            if a[-n:] == b[:n]:
                return True
        return False

    def _merge_subtitle_buffer(self):
        if not self.subtitle_buffer:
            return

        # 청크 경계에서 생긴 중첩 문장을 병합해 자막 깜빡임과 반복을 줄인다.
        self.subtitle_buffer.sort(key=lambda x: x.start_time)
        merged = [self.subtitle_buffer[0]]

        for event in self.subtitle_buffer[1:]:
            prev = merged[-1]
            is_close = event.start_time <= prev.end_time + 0.45

            if is_close and self._is_overlapped_sentence(prev.text, event.text):
                if len(self._normalize_text(event.text)) > len(self._normalize_text(prev.text)):
                    prev.text = event.text
                prev.end_time = max(prev.end_time, event.end_time)
                continue

            merged.append(event)

        self.subtitle_buffer = merged

    def clear_subtitles(self):
        self.subtitle_buffer.clear()
        self.prev_display_text = ""
        self.current_display_text = ""
        self.subtitle_var.set("자막 버퍼를 비웠습니다.")
        self.time_var.set("00:00.00 ~ 00:00.00")

    def save_latency_log(self):
        if not self.latency_samples:
            messagebox.showinfo("안내", "저장할 지연 로그가 없습니다.")
            return

        output = Path("latency_log.txt")
        avg = sum(self.latency_samples) / len(self.latency_samples)
        lines = [
            f"sample_count={len(self.latency_samples)}",
            f"avg_latency={avg:.4f}",
            "",
            "samples:",
        ]
        lines.extend(f"{x:.4f}" for x in self.latency_samples)
        output.write_text("\n".join(lines), encoding="utf-8")
        messagebox.showinfo("저장 완료", f"{output.resolve()} 에 저장했습니다.")

    @staticmethod
    def _fmt(sec: float) -> str:
        m = int(sec // 60)
        s = sec % 60
        return f"{m:02d}:{s:05.2f}"

    def _on_close(self):
        self._stop_worker()
        self.sync_server.stop()
        self.root.destroy()


def main():
    root = tk.Tk()

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    BrowserSyncWhisperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

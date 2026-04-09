"""
YouTube 자막 동기화 앱

Chrome Extension이 보내는 재생 상태를 수신하고,
같은 영상의 오디오를 다시 추출해 Whisper로 전사한 뒤,
현재 브라우저 시간에 맞는 자막만 Tk 창에 표시한다.

구성 요소는 다음과 같다.
- Python 서버: 127.0.0.1:8765 에서 /health 와 /sync 를 처리한다.
- Chrome Extension: YouTube 탭에서 currentTime, paused, url, title 을 보낸다.
- Whisper 파이프라인: yt-dlp 와 ffmpeg 로 오디오를 받아 전사한다.

실행 방법:
python browser_sync_whisper_test.py
"""

import json
import os
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

CHUNK_SECONDS = 2.2
OVERLAP_SECONDS = 0.6

CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)
OVERLAP_SAMPLES = int(SAMPLE_RATE * OVERLAP_SECONDS)
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE

MODEL_NAME = "base"
LANGUAGE_HINT = os.getenv("WHISPER_LANGUAGE_HINT", "").strip() or None
BEAM_SIZE = max(1, int(os.getenv("WHISPER_BEAM_SIZE", "5")))
TEMPERATURE = 0.0

MIN_TEXT_LENGTH = 2
DUPLICATE_TIME_WINDOW = 1.5
SUBTITLE_HOLD_SECONDS = 0.8
LATE_ACCEPT_SECONDS = 2.5
MAX_BUFFER_SECONDS = 180.0
SYNC_OFFSET_SECONDS = 0.05
DROP_OLD_EVENT_SECONDS = 2.0
OUTPUT_PREBUFFER_SECONDS = 6.0
EMIT_DEDUP_WINDOW_SECONDS = 2.5
SHORT_TEXT_DEDUP_WINDOW_SECONDS = 6.0
SHORT_TEXT_MAX_LEN = 8

SYNC_SERVER_HOST = "127.0.0.1"
SYNC_SERVER_PORT = 8765
BROWSER_STALE_SECONDS = 2.0
YTDLP_TIMEOUT = 30

# 오디오 청크와 자막 유지 시간을 정하는 기본 상수들이다.
# CHUNK_SECONDS 는 한 번에 Whisper에 넣는 오디오 길이, OVERLAP_SECONDS 는 이전 청크와 겹쳐 읽는 구간이다.
# 현재는 초반 정확도를 우선해서 청크를 조금 늘렸고, overlap 은 문장 절단을 줄일 정도만 남겼다.
# beam size 도 기본값을 3으로 올려서 더 안정적인 문장 선택을 노린다.
# OUTPUT_PREBUFFER_SECONDS 는 화면에 자막을 내보내기 전에 미리 분석해 두는 최소 선행 구간이다.
# 전체 영상을 완전히 다 스캔한 뒤 보여주는 방식보다, 이 선행 버퍼를 채운 뒤 출력하는 방식이 실시간성과 정확도 균형이 좋다.


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
    # Windows 환경마다 ffmpeg 설치 위치가 다를 수 있으므로,
    # 환경 변수와 일반적인 설치 경로를 순서대로 확인한다.
    env_path = os.getenv("FFMPEG_PATH", "").strip()
    candidates = [
        env_path,
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
        "PowerShell/VS Code 터미널을 재시작한 뒤 다시 시도하거나, FFMPEG_PATH 또는 PATH에 ffmpeg.exe 경로를 추가하세요."
    )


@dataclass
class SubtitleEvent:
    start_time: float
    end_time: float
    text: str
    created_monotonic: float


# 브라우저가 보낸 재생 정보와 Whisper가 만든 자막 구간을 각각 구조화해 보관한다.
# BrowserState 는 현재 영상 위치와 탭 정보를, SubtitleEvent 는 자막 타이밍과 텍스트를 담는다.
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

        # 백그라운드 스레드에서 받은 상태를 Tk 메인 스레드로 넘기기 위한 큐들이다.
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
        self.transcription_ready = False
        self.subtitle_ready = False
        self.output_ready = False

        # 브라우저가 마지막으로 보낸 재생 상태를 기준으로 자막을 표시한다.
        self.browser_current_time: float = 0.0
        self.browser_paused: bool = True
        self.browser_last_update_monotonic: Optional[float] = None
        self.browser_url: str = ""
        self.browser_title: str = ""

        # Whisper 결과를 잠시 보관한 뒤 현재 재생 시간과 맞는 자막만 고른다.
        self.subtitle_buffer = []
        self.last_displayed_text = ""
        self.last_displayed_start = -999.0
        self.last_emitted_normalized_text = ""
        self.last_emitted_start = -999.0
        self.prev_display_text = ""
        self.current_display_text = ""
        self.last_subtitle_update_monotonic: Optional[float] = None
        self.last_restart_worker_monotonic: float = 0.0  # 마지막 워커 재시작 시간 기록

        self.last_browser_current_time: Optional[float] = None
        self.last_browser_update_monotonic: Optional[float] = None
        self.first_video_received: bool = False  # 처음 영상 수신 여부 (시작점 안내용)

        self.latency_samples = []

        self._build_ui()
        self.sync_server.start()
        self._load_model_async()
        self.root.after(100, self._poll_queues)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # UI는 현재 상태를 빠르게 확인할 수 있도록 요약 정보와 자막 영역만 둔다.
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="브라우저 연동 안내: youtube_sync_extension 을 로드하면 아래 상태가 자동으로 갱신됩니다.",
        ).pack(anchor="w")

        self.status_var = tk.StringVar(value="초기화 중...")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))

        self.video_var = tk.StringVar(value="영상: 없음")
        ttk.Label(top, textvariable=self.video_var, font=("Arial", 10, "bold")).pack(anchor="w", pady=(6, 0))

        info_frame = ttk.Frame(top)
        info_frame.pack(fill="x", pady=(8, 0))

        self.browser_time_var = tk.StringVar(value="브라우저 시간: 00:00.00")
        self.model_var = tk.StringVar(value=f"모델: {MODEL_NAME} (로딩 중)")
        self.transcription_var = tk.StringVar(value="전사 상태: 대기 중")
        self.subtitle_state_var = tk.StringVar(value="자막 상태: 대기 중")
        self.output_state_var = tk.StringVar(value="출력 상태: 대기 중")
        self.latency_var = tk.StringVar(value="평균 표시 지연: 측정 전")
        self.analysis_hint_var = tk.StringVar(
            value="초반 자막 작업에 시간이 소요될 수 있습니다. 준비가 끝나면 자동으로 표시됩니다."
        )
        self.ready_banner_var = tk.StringVar(value="자막 출력 준비 상태: 분석 중")

        ttk.Label(info_frame, textvariable=self.browser_time_var).pack(side="left")
        ttk.Label(info_frame, textvariable=self.model_var).pack(side="left", padx=(20, 0))
        ttk.Label(info_frame, textvariable=self.transcription_var).pack(side="left", padx=(20, 0))
        ttk.Label(info_frame, textvariable=self.subtitle_state_var).pack(side="left", padx=(20, 0))
        ttk.Label(info_frame, textvariable=self.output_state_var).pack(side="left", padx=(20, 0))
        ttk.Label(info_frame, textvariable=self.latency_var).pack(side="left", padx=(20, 0))

        ttk.Label(
            top,
            textvariable=self.analysis_hint_var,
            font=("Arial", 11, "bold"),
            foreground="#c26d00",
        ).pack(anchor="w", pady=(6, 0))

        self.ready_banner_label = tk.Label(
            top,
            textvariable=self.ready_banner_var,
            font=("Arial", 12, "bold"),
            bg="#fff1cc",
            fg="#8a5a00",
            padx=10,
            pady=6,
            anchor="w",
        )
        self.ready_banner_label.pack(fill="x", pady=(6, 0))

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

        self.subtitle_var = tk.StringVar(value="브라우저에서 유튜브를 재생하면 자막이 여기에 표시됩니다.")
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
        # Whisper 모델 로딩은 시간이 걸리므로 별도 스레드에서 처리해 UI 응답을 유지한다.
        # 현재는 테스트 안정성을 위해 base 모델로 고정한다.
        # 향후 외부 번역/자막 파이프라인으로 넘길 계획이 있으므로, 지금은 무거운 모델보다 동작 안정성을 우선한다.
        def load():
            try:
                self.model = whisper.load_model(MODEL_NAME)
                self.root.after(0, lambda: self.model_var.set(f"모델: {MODEL_NAME} (준비 완료)"))
                self.root.after(0, self._start_worker_if_ready)
            except Exception as e:
                self.root.after(0, lambda: self.model_var.set(f"모델 로딩 실패: {e}"))

        threading.Thread(target=load, daemon=True).start()

    def _start_worker_if_ready(self):
        # 브라우저 상태는 이미 들어왔는데 모델만 늦게 준비된 경우,
        # 현재 탭의 URL과 시간을 기준으로 전사를 시작한다.
        # 이 분기를 두는 이유는 탭이 먼저 열리고, Whisper 모델이 나중에 준비되는 순서를 안전하게 처리하기 위해서다.
        if self.model is None or not self.browser_url:
            return

        if (
            self.worker_thread is not None
            and self.worker_thread.is_alive()
            and self.current_video_url == self.browser_url
        ):
            return

        self._maybe_notify_initial_playhead(self.browser_current_time)

        self._restart_worker_for_video(self.browser_url, start_offset_seconds=self.browser_current_time)

    def _maybe_notify_initial_playhead(self, current_time: float):
        if self.first_video_received:
            return

        self.first_video_received = True
        if current_time <= 0.5:
            return

        notice = (
            "영상이 이미 재생 중(또는 중간 위치)입니다.\n"
            "원활한 자막 출력을 위해 시작지점(0초)으로 맞춰 주세요."
        )
        self.status_var.set("안내: 영상 시작지점(0초)으로 맞추면 자막 정확도가 올라갑니다.")
        messagebox.showinfo("자막 시작 안내", notice)

    def _set_ready_banner(self, ready: bool):
        if ready:
            self.ready_banner_var.set("자막 출력 준비 상태: 준비 완료")
            self.ready_banner_label.configure(bg="#dff6dd", fg="#166534")
        else:
            self.ready_banner_var.set("자막 출력 준비 상태: 분석 중")
            self.ready_banner_label.configure(bg="#fff1cc", fg="#8a5a00")

    def _handle_browser_state(self, state: BrowserState):
        # YouTube는 SPA처럼 동작하므로 URL, 현재 시간, paused 상태를 계속 갱신해야 한다.
        # URL 이 바뀌었는데 모델이 아직 없으면, 상태만 기억해 두고 모델 준비 직후 자동 시작하도록 넘긴다.
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
            
            # Seek 감지: 너무 민감하지 않도록 임계값을 크게 설정
            # 사용자가 자막을 보고 있는 중이면 더 확실한 seek만 감지 (임계값 상향)
            seek_threshold = 7.0 if self.subtitle_buffer else 5.0
            
            seeked_while_playing = (not state.paused) and abs(dt_media - dt_wall) > seek_threshold
            jumped_back = dt_media < -2.0
            
            # 마지막 재시작 이후 최소 15초가 지나야 다시 재시작 허용 (연속 재시작 방지)
            now_monotonic = time.monotonic()
            can_restart = (now_monotonic - self.last_restart_worker_monotonic) > 15.0

            # 안내를 보고 시작지점으로 되감는 경우는 즉시 반영해야 하므로 쿨다운 예외 처리
            rewound_to_start = (
                state.current_time <= 1.5
                and self.last_browser_current_time >= 8.0
                and dt_media <= -5.0
            )

            should_restart = (seeked_while_playing and can_restart) or jumped_back and (can_restart or rewound_to_start)
            
            if should_restart:
                self.status_queue.put("브라우저 seek 감지 - 시간축 재정렬")
                if state.url and state.url == prev_url and self.model is not None:
                    self._restart_worker_for_video(state.url, start_offset_seconds=state.current_time)

        self.last_browser_current_time = state.current_time
        self.last_browser_update_monotonic = state.received_monotonic

        if state.url and state.url != prev_url:
            # 새 영상으로 넘어가면 이전 영상의 시간축과 자막 버퍼를 완전히 버리고,
            # 현재 영상 currentTime 을 기준으로 워커를 즉시 다시 시작한다.
            self.last_browser_current_time = None
            self.last_browser_update_monotonic = None
            if self.model is None:
                self.status_var.set("새 영상 감지됨. 모델 로딩이 끝나면 전사를 시작합니다.")
            else:
                self._maybe_notify_initial_playhead(state.current_time)
                self._restart_worker_for_video(state.url, start_offset_seconds=state.current_time)
                return

        self._start_worker_if_ready()

    def _restart_worker_for_video(self, video_url: str, start_offset_seconds: float = 0.0):
        # 새 영상이나 seek 변화가 있으면 이전 워커를 중단하고 새 스트림 기준으로 다시 시작한다.
        # 자막 타이밍이 꼬이는 가장 흔한 원인이므로, 영상 전환 시에는 항상 세션을 새로 만든다.
        self.last_restart_worker_monotonic = time.monotonic()  # 워커 재시작 시간 기록 (연쇄 재시작 방지)
        
        self._stop_worker()
        self._drain_queue(self.subtitle_queue)
        self.transcription_ready = False
        self.subtitle_ready = False
        self.output_ready = False
        self.transcription_var.set("전사 상태: 준비 중")
        self.subtitle_state_var.set("자막 상태: 대기 중")
        self.output_state_var.set("출력 상태: 선행 분석 중")
        self.analysis_hint_var.set("초반 자막 작업에 시간이 소요될 수 있습니다. 현재 영상을 미리 분석 중입니다.")
        self._set_ready_banner(False)

        self.current_video_url = video_url
        self.current_stream_url = ""
        self.current_stream_start_offset = max(0.0, float(start_offset_seconds))
        self.transcribe_session_id += 1
        self.last_displayed_text = ""
        self.last_displayed_start = -999.0
        self.last_emitted_normalized_text = ""
        self.last_emitted_start = -999.0
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
        # 워커가 다음 청크를 읽기 전에 멈추도록 종료 플래그를 세운다.
        # 실제 subprocess 종료는 _stream_and_transcribe 마지막에서 처리되지만, 여기서는 즉시 중단 신호만 준다.
        self.worker_stop_flag.set()
        self.current_stream_url = ""

    @staticmethod
    def _drain_queue(target_queue: queue.Queue):
        while True:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                break


    def _worker_main(self, video_url: str, session_id: int, start_offset_seconds: float):
        try:
            direct_url = self._get_direct_audio_url(video_url)
            self.current_stream_url = direct_url
            self._stream_and_transcribe(direct_url, session_id, start_offset_seconds)
        except Exception as e:
            self.status_queue.put(f"오디오 워커 오류: {e}")

    def _get_direct_audio_url(self, video_url: str) -> str:
        api_url = _extract_stream_url_with_python_api(video_url)
        if api_url:
            return api_url

        # 최근 YouTube 정책 변화에 대응하기 위해 여러 전략을 시도한다.
        # 각 전략은 서로 다른 형식과 옵션을 조합한다.
        strategies = [
            [sys.executable, "-m", "yt_dlp", "-q", "--no-playlist", "-f", "ba", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "-q", "-f", "ba/b", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "url", "-f", "ba/b", video_url],
            [sys.executable, "-m", "yt_dlp", "-f", "ba/b", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "-f", "b", "-g", video_url],
        ]

        errors = []
        for i, cmd in enumerate(strategies, 1):
            try:
                # 타임아웃을 더 길게 해서 느린 네트워크도 지원
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=YTDLP_TIMEOUT + 10,
                )

                direct_url = _first_http_url(result.stdout)
                if direct_url:
                    return direct_url

                direct_url = _first_http_url(result.stderr)
                if direct_url:
                    return direct_url

                if result.returncode != 0:
                    stderr = result.stderr.strip()[:200]
                    errors.append(f"시도 {i} 실패 (RC={result.returncode}): {stderr}")
                else:
                    errors.append(f"시도 {i}: 출력 없음")
                    
            except subprocess.TimeoutExpired:
                errors.append(f"시도 {i}: 타임아웃 ({YTDLP_TIMEOUT+10}s)")
            except FileNotFoundError:
                errors.append(f"시도 {i}: yt-dlp 찾을 수 없음")
            except Exception as e:
                errors.append(f"시도 {i}: {str(e)[:100]}")

        error_msg = "\n- ".join(errors) if errors else "알 수 없는 오류"

        # 마지막 폴백
        api_fallback_url = _extract_stream_url_with_python_api(video_url)
        if api_fallback_url:
            return api_fallback_url

        raise RuntimeError(
            f"YouTube 오디오 스트림 URL을 추출할 수 없습니다.\n\n"
            f"시도 결과:\n- {error_msg}\n\n"
            f"확인 사항:\n"
            f"1. YouTube URL이 정확한지 확인 (예: https://www.youtube.com/watch?v=...)\n"
            f"2. 영상이 공개 상태이고 재생 가능한지 확인\n"
            f"3. 인터넷 연결 상태 확인\n"
            f"4. yt-dlp 업데이트: pip install -U yt-dlp\n"
            f"5. 관리자 권한으로 실행해보기\n\n"
            f"분석 중인 URL: {video_url}"
        )

    def _stream_and_transcribe(self, audio_url: str, session_id: int, start_offset_seconds: float):
        # ffmpeg 출력은 비디오의 절대 시간축에 맞춰야 하므로,
        # 현재 영상 시작 시점을 기준으로 누적 시간을 계산한다.
        # start_offset_seconds 를 ffmpeg 와 내부 누적 시간 양쪽에 함께 반영해야,
        # 중간 시점부터 시작한 경우에도 자막 시간이 브라우저 currentTime 과 맞는다.
        # -re 옵션을 쓰지 않아 가능한 빨리 오디오를 읽고 전사하면,
        # 재생 시간보다 앞서 자막을 준비해 둘 수 있다.
        ffmpeg_executable = _resolve_ffmpeg_executable()
        ffmpeg_cmd = [
            ffmpeg_executable,
            "-loglevel", "error",
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

        try:
            process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            self.status_queue.put(f"FFmpeg 시작 오류: {e}")
            return

        if process.stdout is None:
            self.status_queue.put("FFmpeg stdout 파이프를 열지 못했습니다.")
            return

        overlap_audio = np.zeros(0, dtype=np.float32)
        processed_audio_seconds = float(start_offset_seconds)
        consecutive_empty_reads = 0

        while not self.worker_stop_flag.is_set():
            if session_id != self.transcribe_session_id:
                break

            try:
                raw = self._read_exact(process.stdout, CHUNK_BYTES)
                if not raw:
                    consecutive_empty_reads += 1
                    if consecutive_empty_reads > 10:
                        # FFmpeg 프로세스가 이미 종료된 경우 stderr 요약을 남긴다.
                        if process.poll() is not None and process.stderr is not None:
                            try:
                                stderr_text = process.stderr.read().decode("utf-8", errors="replace").strip()
                            except Exception:
                                stderr_text = ""
                            if stderr_text:
                                self.status_queue.put(f"FFmpeg 종료: {stderr_text[:220]}")
                        break
                    else:
                        # 일시적인 지연, 조금 기다렸다가 재시도
                        time.sleep(0.1)
                        continue
                else:
                    consecutive_empty_reads = 0

            except Exception as e:
                self.status_queue.put(f"오디오 스트림 읽기 오류: {str(e)[:150]}")
                break

            current_chunk = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            audio_for_stt = np.concatenate([overlap_audio, current_chunk]) if overlap_audio.size > 0 else current_chunk

            chunk_base_time = max(0.0, processed_audio_seconds - OVERLAP_SECONDS)

            # Whisper 는 한 번의 추론마다 청크 전체를 다시 해석하므로,
            # 청크 길이가 짧을수록 첫 자막이 더 빨리 나오고, beam_size 가 작을수록 추론이 더 빠르다.
            # language 를 고정하면 언어 추정 오차를 줄일 수 있고, no_speech_threshold 는 무음 구간 판정을 돕는다.
            try:
                result = self.model.transcribe(
                    audio_for_stt,
                    fp16=False,
                    language=LANGUAGE_HINT,
                    verbose=False,
                    condition_on_previous_text=False,
                    beam_size=BEAM_SIZE,
                    patience=1.0,
                    temperature=TEMPERATURE,
                    no_speech_threshold=0.65,
                    logprob_threshold=-1.5,
                    compression_ratio_threshold=2.4,
                )
            except Exception as e:
                self.status_queue.put(f"Whisper 전사 오류: {str(e)[:150]}")
                break

            if not self.transcription_ready:
                self.transcription_ready = True
                self.root.after(0, lambda: self.transcription_var.set("전사 상태: 준비 완료"))
                self.root.after(0, lambda: self.status_var.set("전사 준비 완료. 자막 출력 준비 중..."))

            if (not self.output_ready) and processed_audio_seconds >= (start_offset_seconds + OUTPUT_PREBUFFER_SECONDS):
                self.output_ready = True
                self.root.after(0, lambda: self.output_state_var.set("출력 상태: 준비 완료"))
                self.root.after(0, lambda: self.status_var.set("자막 출력 준비 완료. 현재 시간축에 맞춰 표시합니다."))
                self.root.after(0, lambda: self.analysis_hint_var.set("준비가 완료되었습니다. 현재 시간축 기준으로 자막을 표시합니다."))
                self.root.after(0, lambda: self._set_ready_banner(True))

            for seg in result.get("segments", []):
                start = float(seg["start"]) + chunk_base_time
                end = float(seg["end"]) + chunk_base_time
                text = seg["text"].strip()

                if len(text) < MIN_TEXT_LENGTH:
                    continue

                if self._is_low_information_text(text):
                    continue

                if not self._segment_passes_quality(seg, text, start, end):
                    continue

                normalized_text = self._normalize_text(text)
                if not normalized_text:
                    continue

                if self._is_temporal_duplicate(normalized_text, start):
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
                self.last_emitted_normalized_text = normalized_text
                self.last_emitted_start = start

                if not self.subtitle_ready:
                    self.subtitle_ready = True
                    self.root.after(0, lambda: self.subtitle_state_var.set("자막 상태: 첫 자막 수신"))

            overlap_audio = current_chunk[-OVERLAP_SAMPLES:].copy() if OVERLAP_SAMPLES > 0 else np.zeros(0, dtype=np.float32)
            processed_audio_seconds += len(current_chunk) / SAMPLE_RATE

        try:
            process.kill()
        except Exception:
            pass

        if processed_audio_seconds > start_offset_seconds:
            self.status_queue.put(f"전사 완료: {processed_audio_seconds - start_offset_seconds:.1f}초 처리됨")

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
        # 큐에 쌓인 상태를 메인 스레드에서 한 번에 반영해 UI 갱신 비용을 줄인다.
        # status_queue 는 서버/워커 상태, browser_state_queue 는 현재 재생 위치, subtitle_queue 는 전사 결과를 담는다.
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
                event = self.subtitle_queue.get_nowait()
                self.subtitle_buffer.append(event)
        except queue.Empty:
            pass

        self._merge_subtitle_buffer()
        self._update_display_from_browser_time()
        self.root.after(100, self._poll_queues)

    def _update_display_from_browser_time(self):
        # 현재 브라우저 시간에 맞는 자막만 골라 보여준다.
        # active_events 는 현재 재생 시각을 덮는 자막, late_candidates 는 조금 늦게 도착한 보정 자막이다.
        if not self.output_ready:
            self.subtitle_var.set("초반 자막 작업에 시간이 소요될 수 있습니다. 분석이 끝나면 자막이 자동으로 표시됩니다.")
            self.time_var.set("00:00.00 ~ 00:00.00")
            return

        if self.browser_last_update_monotonic is not None:
            age = time.monotonic() - self.browser_last_update_monotonic
            if age > BROWSER_STALE_SECONDS:
                self.status_var.set("브라우저 상태 업데이트가 끊겼습니다. 확장 프로그램 연결을 확인하세요.")

        current_play_time = self.browser_current_time
        self.subtitle_buffer.sort(key=lambda x: x.start_time)

        # 현재 재생 시각을 덮는 자막을 우선 보여주고,
        # 없으면 조금 늦게 도착한 자막 중 가장 근접한 것을 보정해서 보여준다.
        active_events = [
            e for e in self.subtitle_buffer
            if e.start_time <= current_play_time + SYNC_OFFSET_SECONDS <= e.end_time + SUBTITLE_HOLD_SECONDS
        ]
        selected = active_events[-1] if active_events else None

        if selected is None:
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

    @staticmethod
    def _is_low_information_text(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        if not normalized:
            return True

        compact = normalized.replace(" ", "")
        if len(compact) < 2:
            return True

        if len(compact) <= 6 and len(set(compact)) == 1:
            return True

        tokens = [token for token in normalized.split(" ") if token]
        if len(tokens) >= 2 and len(tokens) <= 4 and len(set(tokens)) == 1:
            return True

        return False

    @staticmethod
    def _segment_passes_quality(seg: dict, text: str, start: float, end: float) -> bool:
        duration = end - start
        if duration < 0.18:
            return False

        no_speech_prob = float(seg.get("no_speech_prob", 0.0) or 0.0)
        avg_logprob = float(seg.get("avg_logprob", 0.0) or 0.0)
        compression_ratio = float(seg.get("compression_ratio", 0.0) or 0.0)

        # 음성이 아닐 가능성이 높으면 제외
        if no_speech_prob >= 0.88:
            return False
        
        # 짧은 세그먼트는 신뢰도가 낮으면 제외 (배경음/노이즈 필터)
        if duration < 1.0 and avg_logprob <= -1.5:
            return False
        
        # 일반적인 경우 신뢰도 체크: 빠른 말을 허용하기 위해 -2.5로 완화
        if avg_logprob <= -2.5:
            return False
            
        if compression_ratio >= 3.2:
            return False

        # 한 글자 반복 잡음(예: ㅋㅋㅋㅋ, 아아아) 재확인
        compact = re.sub(r"\s+", "", text.strip().lower())
        if compact and len(compact) <= 8 and len(set(compact)) == 1:
            return False

        # 매우 짧은 세그먼트(0.2~0.5초) + 영어 1-2글자 + 낮은 신뢰도 = 반주 노이즈 가능성
        if 0.2 <= duration <= 0.5:
            compact_text = re.sub(r"[^a-z]", "", text.lower())
            if 1 <= len(compact_text) <= 2 and avg_logprob <= -1.8:
                return False

        return True

    def _is_temporal_duplicate(self, normalized_text: str, start: float) -> bool:
        compact_len = len(normalized_text.replace(" ", ""))
        window = SHORT_TEXT_DEDUP_WINDOW_SECONDS if compact_len <= SHORT_TEXT_MAX_LEN else EMIT_DEDUP_WINDOW_SECONDS
        if (
            normalized_text == self.last_emitted_normalized_text
            and (start - self.last_emitted_start) <= window
        ):
            return True
        return False

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
        # 청크 경계에서 겹치는 자막을 합쳐 중복 표시를 줄인다.
        # Whisper 는 경계 부근에서 같은 문장을 두 번 내는 일이 있어,
        # 시간적으로 가까운 자막끼리 텍스트 중첩을 검사한 뒤 더 긴 결과를 남긴다.
        if not self.subtitle_buffer:
            return

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
        # 현재 자막 표시와 버퍼를 동시에 비워 이전 영상의 흔적을 제거한다.
        self.subtitle_buffer.clear()
        self.last_emitted_normalized_text = ""
        self.last_emitted_start = -999.0
        self.prev_display_text = ""
        self.current_display_text = ""
        self.subtitle_var.set("자막 버퍼를 비웠습니다.")
        self.time_var.set("00:00.00 ~ 00:00.00")

    def save_latency_log(self):
        # 표시 지연 샘플을 파일로 저장하면 나중에 모델/청크 설정 비교에 사용할 수 있다.
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
        # 종료 시에는 워커 스레드와 로컬 HTTP 서버를 함께 정리한다.
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

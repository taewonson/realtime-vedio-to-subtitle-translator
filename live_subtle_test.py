# =====================================================
# 실시간 유튜브 자막 생성 및 번역 애플리케이션
# OpenAI Whisper 모델을 사용한 음성-텍스트 변환
# =====================================================

import subprocess
import shutil
import threading
import queue
import time
import sys
import re
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import numpy as np
import whisper

# =====================================================
# 오디오 설정 (Audio Configuration)
# =====================================================
SAMPLE_RATE = 16000          # 샘플 레이트: 16kHz (Whisper의 권장 사항)
CHANNELS = 1                 # 모노 채널
BYTES_PER_SAMPLE = 2         # s16le 포맷 (16-bit signed integer)
CHUNK_SECONDS = 4.0          # 균형형 프리셋: 정확도와 지연의 타협점
OVERLAP_SECONDS = 1.2        # 균형형 프리셋: 청크 경계 손실 완화
MODEL_NAME = "base"          # 균형형 프리셋: tiny 대비 정확도 향상
LANGUAGE_HINT = None         # 한국어 위주면 "ko"로 고정하면 정확도가 더 좋아질 수 있음

# 계산된 오디오 파라미터들
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)        # 청크당 샘플 수: 32,000
OVERLAP_SAMPLES = int(SAMPLE_RATE * OVERLAP_SECONDS)    # 중복 샘플 수: 8,000
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE          # 청크당 바이트 수: 64,000

# =====================================================
# 자막 필터링 설정 (Subtitle Filtering Settings)
# =====================================================
MIN_TEXT_LENGTH = 2          # 최소 텍스트 길이 (너무 짧은 자막 제거)
DUPLICATE_TIME_WINDOW = 1.5  # 같은 텍스트 반복 감지 시간 범위 (초)
SUBTITLE_HOLD_SECONDS = 1.2  # 자막 유지 시간 (짧은 공백 깜빡임 완화)
LATE_ACCEPT_SECONDS = 10.0   # 늦게 도착한 자막 허용 범위 (지연 보정)
MAX_BUFFER_SECONDS = 120.0   # 메모리 보호를 위한 자막 버퍼 보관 시간
SYNC_OFFSET_SECONDS = 0.25   # 자막이 너무 빠를 때 보정하는 표시 지연


def _first_http_url(text: str) -> str:
    match = re.search(r"https?://\S+", text)
    return match.group(0).strip() if match else ""


def _extract_title_with_python_api(youtube_url: str) -> str:
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
                info = ydl.extract_info(youtube_url, download=False)
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

        is_media_url = (
            "googlevideo.com" in low
            or "videoplayback" in low
            or ".m3u8" in low
            or ".mpd" in low
            or "manifest" in low
            or "mime=" in low
            or "source=youtube" in low
        )
        if is_media_url:
            return url

    return ""


def _extract_stream_url_with_python_api(youtube_url: str) -> str:
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
            "format": "ba/b",
        },
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "b",
        },
    ]

    for opts in option_sets:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
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


def _yt_dlp_runtime_flags() -> list[str]:
    node_path = shutil.which("node")
    if not node_path:
        return []
    return ["--js-runtimes", f"node:{node_path}"]


def _extract_youtube_title(youtube_url: str) -> str:
    api_title = _extract_title_with_python_api(youtube_url)
    if api_title:
        return api_title

    runtime_flags = _yt_dlp_runtime_flags()
    strategies = [
        [sys.executable, "-m", "yt_dlp", *runtime_flags, "--no-playlist", "--print", "title", youtube_url],
        [sys.executable, "-m", "yt_dlp", *runtime_flags, "--no-playlist", "--skip-download", "--print", "title", youtube_url],
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "title", youtube_url],
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download", "--print", "title", youtube_url],
    ]

    for cmd in strategies:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
            title = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
            if title:
                return title
        except Exception:
            pass

    return ""


# =====================================================
# 자막 이벤트 데이터 클래스 (Subtitle Event Data Class)
# =====================================================
class SubtitleEvent:
    """개별 자막의 시작시간, 종료시간, 텍스트를 저장하는 데이터 구조"""
    def __init__(self, start_time: float, end_time: float, text: str):
        self.start_time = start_time      # 자막 시작 시간 (초)
        self.end_time = end_time          # 자막 종료 시간 (초)
        self.text = text.strip()          # 자막 텍스트 (공백 제거)




# =====================================================
# 메인 GUI 애플리케이션 클래스 (Main GUI Application)
# =====================================================
class LiveSubtitleApp:
    """tkinter를 사용한 실시간 유튜브 자막 생성 GUI 애플리케이션"""
    
    def __init__(self, root: tk.Tk):
        """
        애플리케이션 초기화
        
        Args:
            root: tkinter의 루트 윈도우 객체
        """
        self.root = root
        self.root.title("실시간 자막 시험용")
        self.root.geometry("1000x260")
        # Whisper 모델 저장소 (비동기로 로딩됨)
        self.model = None
        
        # 워커 스레드 (유튜브 스트림 처리용)
        self.worker_thread = None

        # 종료 신호 플래그 (워커 스레드 중단용)
        self.stop_flag = threading.Event()
        
        # 스레드 간 통신용 큐
        self.subtitle_queue = queue.Queue()    # 생성된 자막들을 저장
        self.status_queue = queue.Queue()      # 상태 메시지 전달
        self.last_stream_error = ""
        self.transcription_prompt = ""

        # 재생 시간 추적 (ffmpeg 시작 시간 기준)
        self.stream_start_monotonic = None
        
        # 중복 자막 방지용 변수들
        self.last_displayed_text = ""          # 마지막으로 표시된 자막 텍스트
        self.last_displayed_start = -999.0     # 마지막 자막의 시작 시간

        # UI 표시용 자막 버퍼 (큐에서 꺼낸 이벤트를 유지)
        self.subtitle_buffer = []
        self.timeline_anchor_monotonic = None

        # 2줄 자막 표시 상태
        self.prev_display_text = ""
        self.current_display_text = ""

        # 일시정지 상태 관리
        self.is_paused = False
        self.pause_started_monotonic = None
        self.accumulated_pause_seconds = 0.0

        # UI 생성 및 모델 비동기 로딩
        self._build_ui()
        self._load_model_async()

        # 100ms 마다 큐 확인 및 UI 업데이트
        self.root.after(100, self._poll_queues)
        
        # 윈도우 종료 시 정리
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """UI 컴포넌트 생성 및 레이아웃 구성"""
        
        # ===== 상단 영역 (Top Section) =====
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        # YouTube URL 입력 레이블
        ttk.Label(top, text="유튜브 URL").pack(anchor="w")
        
        # URL 입력 필드
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(top, textvariable=self.url_var, width=120)
        self.url_entry.pack(fill="x", pady=(4, 8))

        self.video_title_var = tk.StringVar(value="영상 제목: 아직 없음")
        ttk.Label(top, textvariable=self.video_title_var).pack(anchor="w", pady=(0, 6))

        # 제어 버튼 프레임
        controls = ttk.Frame(top)
        controls.pack(fill="x")

        # "시작" 버튼 (스트림 시작 명령)
        self.start_btn = ttk.Button(controls, text="시작", command=self.start_stream)
        self.start_btn.pack(side="left")

        # "정지" 버튼 (스트림 중단 명령, 초기에는 비활성화)
        self.stop_btn = ttk.Button(controls, text="정지", command=self.stop_stream, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        # "일시정지" 버튼 (타임라인 동결/재개)
        self.pause_btn = ttk.Button(controls, text="일시정지", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=(8, 0))

        # 상태 메시지 라벨 (모델 로딩 상태, 전사 진행 상황 등)
        self.status_var = tk.StringVar(value="모델 로딩 중...")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

        # ===== 본문 영역 (Body Section) =====
        body = ttk.Frame(self.root, padding=(10, 5, 10, 10))
        body.pack(fill="both", expand=True)

        # 자막 시간 정보 라벨 (예: 00:05.23 ~ 00:08.45)
        self.time_var = tk.StringVar(value="00:00.00 ~ 00:00.00")
        ttk.Label(body, textvariable=self.time_var, font=("Arial", 12)).pack(anchor="center", pady=(0, 10))

        # 자막 텍스트 표시 라벨 (메인 자막 출력 영역)
        self.subtitle_var = tk.StringVar(value="자막이 여기 표시됩니다.")
        self.subtitle_label = ttk.Label(
            body,
            textvariable=self.subtitle_var,
            anchor="center",
            justify="center",
            font=("Arial", 22),
            wraplength=900,
        )
        self.subtitle_label.pack(fill="both", expand=True)

    def _load_model_async(self):
        """별도의 스레드에서 Whisper 모델을 비동기로 로딩 (UI 블로킹 방지)"""
        def load():
            try:
                self.status_queue.put("Whisper 모델 로딩 시작...")
                # Whisper 모델 다운로드 및 로드
                self.model = whisper.load_model(MODEL_NAME)
                self.status_queue.put(f"Whisper 모델 로딩 완료: {MODEL_NAME}")
            except Exception as e:
                # 모델 로딩 실패 시 에러 메시지 전달
                self.status_queue.put(f"모델 로딩 실패: {e}")

        # 데몬 스레드로 로딩 시작 (메인 프로그램 종료 시 함께 종료)
        threading.Thread(target=load, daemon=True).start()

    def start_stream(self):
        """유튜브 스트림 시작"""
        # 모델이 아직 로딩 중인 경우 경고
        if self.model is None:
            messagebox.showwarning("경고", "Whisper 모델 로딩이 아직 끝나지 않았습니다.")
            return

        # URL 유효성 검사
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("경고", "유튜브 URL을 입력하세요.")
            return

        self.subtitle_var.set("영상 정보를 확인 중...")
        self.time_var.set("00:00.00 ~ 00:00.00")

        title = _extract_youtube_title(url)
        if title:
            self.video_title_var.set(f"영상 제목: {title}")
            self.status_var.set("영상 인식 완료, 오디오 추출을 시작합니다.")
            self.transcription_prompt = title.strip()
        else:
            self.video_title_var.set("영상 제목: 가져오지 못함")
            self.status_var.set("URL은 받았지만 제목 인식에 실패했습니다. 오디오 추출을 계속 시도합니다.")
            self.transcription_prompt = ""

        # 중복 실행 방지
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("안내", "이미 실행 중입니다.")
            return

        # 스트림 시작을 위한 상태 초기화
        self.stop_flag.clear()                         # 중단 플래그 해제
        self.stream_start_monotonic = time.monotonic() # 재생 시간 기준점 설정
        self.last_displayed_text = ""                  # 이전 자막 초기화
        self.last_displayed_start = -999.0             # 이전 자막 시간 초기화
        self.subtitle_buffer.clear()                   # 표시 버퍼 초기화
        self.timeline_anchor_monotonic = None
        self.prev_display_text = ""
        self.current_display_text = ""
        self.is_paused = False
        self.pause_started_monotonic = None
        self.accumulated_pause_seconds = 0.0
        self.last_stream_error = ""
        self.subtitle_var.set("자막 수집 시작...")      # UI 메시지 변경
        self.time_var.set("00:00.00 ~ 00:00.00")       # 시간 표시 초기화
        self.status_var.set("스트림 준비 중...")        # 상태 메시지 변경

        # 워커 스레드 시작 (유튜브 스트림 처리)
        self.worker_thread = threading.Thread(target=self._worker_main, args=(url,), daemon=True)
        self.worker_thread.start()

        # UI 버튼 상태 변경 (시작 버튼 비활성화, 정지 버튼 활성화)
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.pause_btn.configure(state="normal", text="일시정지")

    def stop_stream(self):
        """유튜브 스트림 중지"""
        self.stop_flag.set()                           # 워커 스레드 중단 신호
        self.status_var.set("중지 요청됨...")          # UI 상태 업데이트
        self.start_btn.configure(state="normal")       # 버튼 상태 복원
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled", text="일시정지")
        self.is_paused = False
        self.pause_started_monotonic = None
        self.accumulated_pause_seconds = 0.0
        self.timeline_anchor_monotonic = None

    def toggle_pause(self):
        """자막 타임라인 일시정지/재개"""
        if self.stream_start_monotonic is None:
            return

        if not self.is_paused:
            self.is_paused = True
            self.pause_started_monotonic = time.monotonic()
            self.pause_btn.configure(text="재개")
            self.status_var.set("일시정지됨")
            return

        now = time.monotonic()
        if self.pause_started_monotonic is not None:
            self.accumulated_pause_seconds += now - self.pause_started_monotonic
        self.pause_started_monotonic = None
        self.is_paused = False
        self.pause_btn.configure(text="일시정지")
        self.status_var.set("재개됨")

    def _worker_main(self, youtube_url: str):
        """
        워커 스레드의 메인 루틴
        - YouTube 직접 오디오 URL 추출
        - 오디오 스트림 및 전사(음성-텍스트 변환) 실행
        """
        stream_failed = False
        try:
            direct_url = self._get_direct_audio_url(youtube_url)
            self.status_queue.put("오디오 스트림 URL 확보 완료")
            
            # ffmpeg으로 오디오 스트림을 받아 Whisper로 전사
            self._stream_and_transcribe(direct_url)
        except Exception as e:
            # 오류 발생 시 메시지 전달
            stream_failed = True
            self.last_stream_error = str(e)
            self.status_queue.put(f"오류: {e}")
        finally:
            # 작업 종료 메시지 전달 및 UI 복원
            if self.stop_flag.is_set() and not stream_failed:
                self.status_queue.put("작업 종료 (사용자 중지)")
            elif not stream_failed:
                self.status_queue.put("작업 종료")
            self.root.after(0, lambda: self.start_btn.configure(state="normal"))
            self.root.after(0, lambda: self.stop_btn.configure(state="disabled"))
            self.root.after(0, lambda: self.pause_btn.configure(state="disabled", text="일시정지"))

    def _get_direct_audio_url(self, youtube_url: str) -> str:
        """
        YouTube 영상에서 직접 접근 가능한 오디오 URL 추출.

        Args:
            youtube_url: YouTube 영상 URL

        Returns:
            직접 접근 가능한 오디오 스트림 URL

        Raises:
            RuntimeError: URL 추출 실패 시
        """
        api_url = _extract_stream_url_with_python_api(youtube_url)
        if api_url:
            return api_url

        runtime_flags = _yt_dlp_runtime_flags()
        strategies = [
            [sys.executable, "-m", "yt_dlp", *runtime_flags, "--no-playlist", "--print", "url", "-f", "ba/b", youtube_url],
            [sys.executable, "-m", "yt_dlp", *runtime_flags, "-f", "ba/b", "-g", youtube_url],
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "url", "-f", "ba/b", youtube_url],
            [sys.executable, "-m", "yt_dlp", "-f", "ba/b", "-g", youtube_url],
        ]

        errors = []
        for i, cmd in enumerate(strategies, 1):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)

                direct_url = _first_stream_url(result.stdout)
                if direct_url:
                    return direct_url

                direct_url = _first_stream_url(result.stderr)
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

        api_fallback_url = _extract_stream_url_with_python_api(youtube_url)
        if api_fallback_url:
            return api_fallback_url

        error_msg = "\n- ".join(errors) if errors else "알 수 없는 오류"
        merged = " ".join(errors)
        if "Video unavailable" in merged:
            raise RuntimeError(
                f"입력한 영상에 접근할 수 없습니다: {youtube_url}\n"
                "비공개/삭제/지역제한/연령제한일 수 있습니다."
            )

        raise RuntimeError(
            f"yt-dlp를 사용하여 YouTube 오디오 URL을 추출할 수 없습니다.\n\n"
            f"시도 결과:\n- {error_msg}\n\n"
            f"해결 방법:\n"
            f"1. yt-dlp 최신 버전: pip install -U yt-dlp\n"
            f"2. URL 유효성 확인: {youtube_url}\n"
            f"3. 영상 상태 확인 (비공개/삭제 등)\n"
            f"4. 선택사항: Node.js 설치 시 더 많은 형식 지원"
        )

    def _stream_and_transcribe(self, audio_url: str):
        """
        ffmpeg을 통해 오디오 스트림을 수신하고 Whisper 모델로 전사(음성-텍스트 변환)
        - 청크 단위로 오디오를 처리 (2초씩)
        - 청크 간의 중복(Overlap)으로 끊기는 단어 방지
        - 중복 자막 제거
        
        Args:
            audio_url: ffmpeg이 수신할 오디오 스트림 URL
        """
        lowered_url = audio_url.lower()
        if "github.com/yt-dlp/yt-dlp/wiki" in lowered_url:
            raise RuntimeError("yt-dlp가 스트림 URL 대신 안내 링크를 반환했습니다. 다른 추출 전략으로 재시도하세요.")

        # ffmpeg 명령어 설정
        # - loglevel error: 에러만 출력
        # - reconnect 옵션: 연결 끊김 시 자동 재연결
        # - s16le: 16-bit 리틀 엔디언 PCM 포맷
        # - pipe:1: 표준 출력으로 오디오 데이터 출력
        ffmpeg_executable = _resolve_ffmpeg_executable()
        ffmpeg_cmd = [
            ffmpeg_executable,
            "-loglevel", "error",
            "-re",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", audio_url,
            "-vn",                      # 비디오 제거
            "-ac", str(CHANNELS),       # 채널 수
            "-ar", str(SAMPLE_RATE),    # 샘플 레이트
            "-f", "s16le",              # 포맷
            "pipe:1",                   # 표준 출력으로 출력
        ]

        # ffmpeg 프로세스 시작
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if process.stdout is None:
            raise RuntimeError("ffmpeg stdout 파이프를 열지 못했습니다.")

        # 청크 간의 음성 중복 저장 (다음 청크에서 사용)
        overlap_audio = np.zeros(0, dtype=np.float32)
        
        # 지금까지 처리한 오디오의 총 길이 (초)
        processed_audio_seconds = 0.0

        # 중단 신호가 없을 때까지 반복
        while not self.stop_flag.is_set():
            # 정확히 CHUNK_BYTES 크기만큼 읽기
            raw = self._read_exact(process.stdout, CHUNK_BYTES)
            if not raw:
                # ffmpeg가 종료되었는지 확인하고, 종료된 경우 stderr를 함께 보고한다.
                return_code = process.poll()
                if return_code is not None:
                    stderr_text = ""
                    if process.stderr is not None:
                        try:
                            stderr_text = process.stderr.read().decode("utf-8", errors="ignore").strip()
                        except Exception:
                            stderr_text = ""

                    if return_code == 0:
                        raise RuntimeError("오디오 스트림이 종료되었습니다. (영상 종료 또는 입력 스트림 종료)")

                    if stderr_text:
                        raise RuntimeError(
                            f"ffmpeg 입력 스트림 실패 (rc={return_code})\n"
                            f"세부 오류: {stderr_text[-500:]}"
                        )

                    raise RuntimeError(f"ffmpeg 입력 스트림 실패 (rc={return_code})")

                # 아직 실행 중인데 빈 데이터가 온 경우 잠시 대기 후 계속 시도
                time.sleep(0.05)
                continue

            # 바이너리 데이터를 float32 형식의 PCM 오디오로 변환 (-1.0 ~ 1.0 범위)
            current_chunk = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0

            # 이전 청크의 중복 부분과 현재 청크를 연결
            if overlap_audio.size > 0:
                audio_for_stt = np.concatenate([overlap_audio, current_chunk])
            else:
                audio_for_stt = current_chunk

            # 현재 청크의 시작 시간 (중복을 고려하여 계산)
            chunk_base_time = max(0.0, processed_audio_seconds - OVERLAP_SECONDS)

            # 상태 메시지 갱신 (현재 처리 시간)
            self.status_queue.put(f"전사 중... 기준시각 {chunk_base_time:.2f}s")

            # Whisper로 음성 전사
            # - fp16=False: 32비트 정밀도 사용 (정확도 향상)
            # - language=LANGUAGE_HINT: 필요시 언어 고정
            # - condition_on_previous_text=True: 이전 청크 문맥 유지
            result = self.model.transcribe(
                audio_for_stt,
                fp16=False,
                language=LANGUAGE_HINT,
                initial_prompt=self.transcription_prompt or None,
                verbose=False,
                condition_on_previous_text=True,
                beam_size=5,
                temperature=0,
                no_speech_threshold=0.55,
                logprob_threshold=-1.0,
                compression_ratio_threshold=2.4,
            )

            # 인식된 각 세그먼트(문장) 처리
            for seg in result.get("segments", []):
                start = float(seg["start"]) + chunk_base_time
                end = float(seg["end"]) + chunk_base_time
                text = seg["text"].strip()

                # 필터링 1: 너무 짧은 텍스트 제거
                if len(text) < MIN_TEXT_LENGTH:
                    continue

                # 필터링 2: 중복 자막 제거
                # (같은 텍스트가 1.5초 이내에 반복되면 무시)
                if (
                    text == self.last_displayed_text
                    and abs(start - self.last_displayed_start) < DUPLICATE_TIME_WINDOW
                ):
                    continue

                # 큐에 자막 이벤트 추가 (메인 스레드에서 처리)
                self.subtitle_queue.put(SubtitleEvent(start, end, text))

            # 다음 청크를 위한 overlap 보존 (마지막 0.5초)
            if OVERLAP_SAMPLES > 0:
                overlap_audio = current_chunk[-OVERLAP_SAMPLES:].copy()
            else:
                overlap_audio = np.zeros(0, dtype=np.float32)

            # 처리한 오디오 길이 업데이트 (실제 수신 길이 기준)
            processed_audio_seconds += len(current_chunk) / SAMPLE_RATE

        # 종료 시 ffmpeg 프로세스 정리
        try:
            process.kill()
        except Exception:
            pass

    @staticmethod
    def _read_exact(pipe, size: int) -> bytes:
        """
        파이프에서 정확히 'size' 바이트만큼 읽기
        - read()가 한 번에 요청한 크기를 다 읽지 않을 수 있으므로 추가 처리 필요
        
        Args:
            pipe: 읽을 파이프 객체
            size: 읽을 바이트 수
            
        Returns:
            읽은 바이트 데이터 (size보다 작을 수 있음 - EOF 시)
        """
        data = b""
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                # EOF 도달 또는 읽기 실패
                break
            data += chunk
        return data

    def _poll_queues(self):
        """
        메인 스레드에서 100ms마다 실행되는 큐 폴링 함수
        - 상태 메시지 큐에서 메시지 수신
        - 자막 이벤트 큐에서 자막 수신 및 화면 표시 타이밍 처리
        """
        # ===== 상태 메시지 처리 =====
        try:
            while True:
                status = self.status_queue.get_nowait()
                self.status_var.set(status)
        except queue.Empty:
            pass

        # ===== 자막 큐 -> 버퍼 =====
        try:
            while True:
                self.subtitle_buffer.append(self.subtitle_queue.get_nowait())
        except queue.Empty:
            pass

        self._merge_subtitle_buffer()

        # 스트림이 실행 중인 경우에만 타임라인 기준으로 선택
        if self.stream_start_monotonic is not None:
            now = time.monotonic()
            paused_for = 0.0
            if self.is_paused and self.pause_started_monotonic is not None:
                paused_for = now - self.pause_started_monotonic

            # 첫 자막 도착 시점을 기준으로 타임라인 앵커를 잡아 시작 지연 드리프트를 줄임
            if self.timeline_anchor_monotonic is None and self.subtitle_buffer:
                self.timeline_anchor_monotonic = now - self.subtitle_buffer[0].start_time

            anchor = self.timeline_anchor_monotonic or self.stream_start_monotonic
            current_play_time = max(0.0, now - anchor - self.accumulated_pause_seconds - paused_for - SYNC_OFFSET_SECONDS)

            # 버퍼를 시간순으로 유지
            self.subtitle_buffer.sort(key=lambda x: x.start_time)

            active_events = [
                e for e in self.subtitle_buffer
                if e.start_time <= current_play_time <= e.end_time + SUBTITLE_HOLD_SECONDS
            ]
            selected = active_events[-1] if active_events else None

            # 전사 지연으로 늦게 들어온 자막은 짧은 범위에서 허용
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

                top_line = self.prev_display_text if self.prev_display_text else " "
                self.subtitle_var.set(f"{top_line}\n{self.current_display_text}")
                self.time_var.set(f"{self._fmt(selected.start_time)} ~ {self._fmt(selected.end_time)}")
                self.last_displayed_text = selected.text
                self.last_displayed_start = selected.start_time
            else:
                if self.current_display_text:
                    self.prev_display_text = self.current_display_text
                self.current_display_text = ""
                self.subtitle_var.set("\n")
                self.time_var.set("00:00.00 ~ 00:00.00")

            # 오래된 이벤트 정리
            min_keep_time = current_play_time - MAX_BUFFER_SECONDS
            self.subtitle_buffer = [e for e in self.subtitle_buffer if e.end_time >= min_keep_time]

        # 다시 100ms 후에 호출되도록 스케줄
        self.root.after(100, self._poll_queues)

    @staticmethod
    def _normalize_text(text: str) -> str:
        t = re.sub(r"\s+", " ", text.strip().lower())
        return re.sub(r"[^\w\s]", "", t)

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

    @staticmethod
    def _fmt(sec: float) -> str:
        """
        시간(초)을 MM:SS.MS 형식으로 변환
        
        Args:
            sec: 시간 (초)
            
        Returns:
            형식화된 시간 문자열 (예: 01:23.45)
        """
        m = int(sec // 60)          # 분
        s = sec % 60                # 초
        return f"{m:02d}:{s:05.2f}"  # 분:초.밀리초 형식

    def _on_close(self):
        """윈도우 종료 시 정리 작업"""
        self.stop_flag.set()        # 워커 스레드 중단 신호
        self.root.destroy()         # 윈도우 종료


# =====================================================
# 애플리케이션 진입점 (Main Entry Point)
# =====================================================
def main():
    """애플리케이션 시작"""
    # tkinter 루트 윈도우 생성
    root = tk.Tk()
    
    # UI 테마 설정 (선택 사항)
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        # 테마 지정 실패 시 기본 테마 사용
        pass
    
    # 애플리케이션 인스턴스 생성 및 실행
    app = LiveSubtitleApp(root)
    root.mainloop()


# 프로그램 실행 시작점
if __name__ == "__main__":
    main()
from __future__ import annotations
"""ffmpeg 기반 미디어 디코딩 헬퍼.

원격 미디어 URL을 Google Speech 스트리밍 인식이 기대하는 오디오 포맷의
고정 크기 PCM 청크로 변환합니다.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Generator, Optional


class FFmpegPCMStreamer:
    """해석된 YouTube 오디오 스트림을 16kHz 모노 PCM 바이트로 변환합니다.

    Google Speech 스트리밍은 요청 설정과 일치하는 raw 오디오 바이트를
    기대하므로, 이 클래스가 ffmpeg 프로세스 세부사항을 숨기고
    예측 가능한 PCM 청크를 제공합니다.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        bytes_per_sample: int = 2,
        chunk_seconds: float = 0.4,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_per_sample = bytes_per_sample
        self.chunk_seconds = chunk_seconds
        self.chunk_bytes = int(sample_rate * chunk_seconds) * channels * bytes_per_sample

    @staticmethod
    def resolve_ffmpeg_path() -> str:
        """환경 변수, PATH, 일반적인 Windows 설치 경로에서 ffmpeg를 찾습니다."""
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
            "ffmpeg 실행 파일을 찾을 수 없습니다. PATH 또는 FFMPEG_PATH를 확인하세요."
        )

    @staticmethod
    def _read_exact(pipe, size: int) -> bytes:
        """스트림이 끝나기 전까지 지정한 바이트 수만큼 최대한 읽습니다."""
        data = b""
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def iter_pcm_chunks(self, input_url: str, start_offset_seconds: float = 0.0) -> Generator[bytes, None, None]:
        """입력 미디어 URL로부터 PCM 프레임을 순차적으로 반환합니다.

        start_offset_seconds는 seek/탐색 등으로 세션이 재시작될 때
        브라우저 currentTime부터 다시 이어받기 위해 사용됩니다.
        """
        ffmpeg = self.resolve_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-ss",
            str(max(0.0, start_offset_seconds)),
            "-i",
            input_url,
            "-vn",
            "-ac",
            str(self.channels),
            "-ar",
            str(self.sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ]

        process: Optional[subprocess.Popen] = None
        try:
            # 현재는 소비하지 않지만, 추후 진단을 위해 stderr 파이프를 유지합니다.
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if process.stdout is None:
                raise RuntimeError("ffmpeg stdout 파이프를 열지 못했습니다.")

            while True:
                raw = self._read_exact(process.stdout, self.chunk_bytes)
                if not raw:
                    break
                yield raw
        finally:
            if process is not None:
                try:
                    process.kill()
                except Exception:
                    pass

from __future__ import annotations
"""YouTube 페이지에서 직접 오디오 스트림 URL을 해석합니다.

파이프라인 후속 단계에서는 ffmpeg가 바로 읽을 수 있는 URL이 필요하므로,
이 모듈은 yt-dlp 호출 전략과 fallback 처리를 캡슐화합니다.
"""

import re
import subprocess
import sys
from typing import Optional


class YouTubeAudioResolver:
    """YouTube 페이지 URL에서 재생 가능한 직접 오디오 URL을 해석합니다.

    Google Cloud API가 YouTube 오디오를 직접 가져오지는 않으므로,
    로컬 앱에서 미디어 스트림을 가져와 PCM으로 변환한 뒤
    Speech-to-Text로 전달해야 합니다.
    """

    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _first_http_url(text: str) -> str:
        match = re.search(r"https?://\S+", text)
        return match.group(0).strip() if match else ""

    def resolve(self, video_url: str) -> str:
        """yt-dlp를 사용해 직접 재생 가능한 미디어 URL을 반환합니다.

        YouTube/yt-dlp 출력은 환경과 포맷에 따라 달라질 수 있어
        여러 전략을 순차적으로 시도합니다.
        """
        commands = [
            # 먼저 오디오 우선 포맷 셀렉터를 시도하고, 이후 더 넓은 fallback을 시도합니다.
            [sys.executable, "-m", "yt_dlp", "-q", "--no-playlist", "-f", "ba", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "-q", "-f", "ba/b", "-g", video_url],
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "url", "-f", "ba/b", video_url],
            [sys.executable, "-m", "yt_dlp", "-f", "b", "-g", video_url],
        ]

        errors: list[str] = []
        for idx, cmd in enumerate(commands, start=1):
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except Exception as exc:
                errors.append(f"시도 {idx}: {exc}")
                continue

            for candidate in (completed.stdout, completed.stderr):
                # 일부 yt-dlp 환경은 stderr에도 URL을 출력하므로
                # 두 출력 스트림을 모두 검사합니다.
                direct = self._first_http_url(candidate or "")
                if direct:
                    return direct

            errors.append(f"시도 {idx}: 직접 URL 추출 실패")

        joined = "\n- ".join(errors) if errors else "알 수 없는 오류"
        raise RuntimeError(
            "YouTube 오디오 스트림 URL을 추출할 수 없습니다.\n"
            f"- {joined}\n"
            "yt-dlp 최신 버전 설치 여부와 영상 접근 가능 여부를 확인하세요."
        )

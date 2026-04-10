from __future__ import annotations
"""Tkinter UI 렌더링 레이어.

이 모듈은 의도적으로 화면 표시 로직만 포함하며,
서비스 의존성을 분리해 유지보수와 테스트를 단순화합니다.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from subtitle.models import SubtitleCue


class MainWindow:
    """로컬 게이트웨이 앱용 단순 운영 UI.

    이 창은 의도적으로 얇은 뷰 계층만 담당합니다.
    오디오/STT/번역 내부 동작은 알지 못하고,
    코디네이터가 제공하는 현재 상태와 자막만 표시합니다.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube Google Cloud Subtitle Sync")
        self.root.geometry("1180x520")

        self.status_var = tk.StringVar(value="초기화 중...")
        self.video_var = tk.StringVar(value="영상: 없음")
        self.browser_time_var = tk.StringVar(value="브라우저 시간: 00:00.00")
        self.language_var = tk.StringVar(value="번역 언어: 미설정")
        self.subtitle_time_var = tk.StringVar(value="00:00.00 ~ 00:00.00")
        self.subtitle_var = tk.StringVar(value="브라우저에서 유튜브를 재생하면 자막이 여기에 표시됩니다.")

        self._build()

    def _build(self) -> None:
        """정적 레이아웃을 구성하고 StringVar 라벨을 연결합니다."""
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Chrome 확장 프로그램이 자동으로 유튜브 재생 상태를 보냅니다.").pack(anchor="w")
        ttk.Label(top, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(top, textvariable=self.video_var, font=("Arial", 10, "bold")).pack(anchor="w", pady=(6, 0))

        info_frame = ttk.Frame(top)
        info_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(info_frame, textvariable=self.browser_time_var).pack(side="left")
        ttk.Label(info_frame, textvariable=self.language_var).pack(side="left", padx=(20, 0))

        body = ttk.Frame(self.root, padding=(10, 10, 10, 10))
        body.pack(fill="both", expand=True)

        ttk.Label(body, textvariable=self.subtitle_time_var, font=("Arial", 12)).pack(anchor="center", pady=(0, 8))
        ttk.Label(
            body,
            textvariable=self.subtitle_var,
            anchor="center",
            justify="center",
            font=("Arial", 24),
            wraplength=1040,
        ).pack(fill="both", expand=True)

    def set_status(self, text: str) -> None:
        """상단의 1줄 운영 상태 문구를 갱신합니다."""
        self.status_var.set(text)

    def set_video(self, title: str) -> None:
        """현재 영상 제목 라벨을 갱신합니다."""
        self.video_var.set(f"영상: {title or '(제목 없음)'}")

    def set_browser_time(self, formatted_time: str) -> None:
        """브라우저 currentTime 표시 문자열을 갱신합니다."""
        self.browser_time_var.set(f"브라우저 시간: {formatted_time}")

    def set_target_language(self, target_language: str) -> None:
        """현재 설정된 번역 목표 언어를 표시합니다."""
        self.language_var.set(f"번역 언어: {target_language}")

    def show_cue(self, cue: Optional[SubtitleCue]) -> None:
        """활성 자막 큐를 표시하고, 없으면 영역을 비웁니다."""
        if cue is None:
            self.subtitle_var.set("\n")
            self.subtitle_time_var.set("00:00.00 ~ 00:00.00")
            return

        text = cue.translated_text or cue.source_text
        self.subtitle_var.set(text)
        self.subtitle_time_var.set(f"{self._fmt(cue.start_time)} ~ {self._fmt(cue.end_time)}")

    @staticmethod
    def _fmt(seconds: float) -> str:
        """초 단위를 mm:ss.xx 형식 문자열로 변환합니다."""
        minutes = int(seconds // 60)
        remain = seconds % 60
        return f"{minutes:02d}:{remain:05.2f}"

    @staticmethod
    def show_error(title: str, text: str) -> None:
        """블로킹 오류 대화상자를 띄우는 편의 래퍼입니다."""
        messagebox.showerror(title, text)

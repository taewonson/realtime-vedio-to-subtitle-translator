"""라즈베리파이 LCD에서 자막을 표시하는 독립 PySide6 화면입니다."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading

from dotenv import load_dotenv
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from language_config import LCD_LANGUAGE_LABEL_TO_CODE, LCD_LANGUAGE_OPTIONS, get_language_label

load_dotenv()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ==========================================
# 통신 및 UI 기본 설정
# ==========================================
PC_IP = os.getenv("SUBTITLE_PC_IP", "127.0.0.1").strip() or "127.0.0.1"
MY_PORT = env_int("SUBTITLE_PI_PORT", 5005)
PC_PORT = env_int("SUBTITLE_PC_COMMAND_PORT", 5006)

LCD_WIDTH, LCD_HEIGHT = 1024, 600
GEOMETRY = os.getenv("SUBTITLE_LCD_GEOMETRY", f"{LCD_WIDTH}x{LCD_HEIGHT}").strip() or f"{LCD_WIDTH}x{LCD_HEIGHT}"
FULLSCREEN = os.getenv("SUBTITLE_LCD_FULLSCREEN", "1").strip().lower() in {"1", "true", "yes", "on"}
FONT_FAMILY = "Malgun Gothic"

THEME = {
    "bg": "#f8f1e8",
    "panel": "#fffaf7",
    "surface": "#e5cfbc",
    "text": "#5a443a",
    "muted": "#8d776a",
    "accent": "#9b6d5b",
    "accent_alt": "#b8866f",
    "pause": "#c7a26f",
    "border": "rgba(181, 160, 147, 0.54)",
}


def make_font(size: int, bold: bool = False) -> QFont:
    font = QFont(FONT_FAMILY, size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
    return font


# 화면 폭을 넘는 긴 단어를 강제로 줄바꿈하기 위한 보조 함수
def chunk_text(value: str, width: int) -> list[str]:
    return [value[index : index + width] for index in range(0, len(value), width)]


# 자막 텍스트를 화면에 맞게 2줄 단위 페이지로 분리
def build_subtitle_pages(text: str, max_chars_per_line: int = 28) -> list[str]:
    if not isinstance(text, str):
        return [""]
    normalized = " ".join(text.strip().split())
    if not normalized:
        return [""]

    words = normalized.split(" ")
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if len(word) <= max_chars_per_line:
            current = word
        else:
            lines.extend(chunk_text(word, max_chars_per_line))

    if current:
        lines.append(current)

    pages: list[str] = []
    for index in range(0, len(lines), 2):
        first = lines[index]
        second = lines[index + 1] if index + 1 < len(lines) else ""
        pages.append((first + "\n" + second).rstrip())

    return pages if pages else [""]


class UdpBridge(QObject):
    payload_received = Signal(object)


class SeekBar(QWidget):
    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current = 0.0
        self._total = 1.0
        self._preview = 0.0
        self._dragging = False
        self.setMinimumHeight(28)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # 현재 재생 위치와 전체 길이를 바탕으로 프로그레스 값을 갱신
    def set_time(self, current: float, total: float) -> None:
        self._total = max(float(total), 0.1)
        if not self._dragging:
            self._current = max(0.0, min(float(current), self._total))
        self.update()

    # 드래그 중에는 미리보기 위치를 우선 사용
    def _value_for_paint(self) -> float:
        return self._preview if self._dragging else self._current

    # 마우스 x 좌표를 시간 값으로 변환
    def _value_from_pos(self, x: float) -> float:
        width = max(1, self.width() - 16)
        ratio = max(0.0, min(1.0, (x - 8.0) / width))
        return ratio * self._total

    # 시간 값을 실제 그릴 x 좌표로 변환
    def _x_from_value(self, value: float) -> float:
        width = max(1, self.width() - 16)
        ratio = 0.0 if self._total <= 0 else max(0.0, min(1.0, value / self._total))
        return 8.0 + ratio * width

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._preview = self._value_from_pos(event.position().x())
            self.update()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging:
            self._preview = self._value_from_pos(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._preview = self._value_from_pos(event.position().x())
            self._current = self._preview
            self._dragging = False
            self.seek_requested.emit(self._current)
            self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        track_rect = QRectF(8.0, self.height() / 2 - 4.0, self.width() - 16.0, 8.0)
        painter.setBrush(QColor(THEME["surface"]))
        painter.drawRoundedRect(track_rect, 4.0, 4.0)

        value = self._value_for_paint()
        fill_x = self._x_from_value(value)
        fill_rect = QRectF(track_rect.left(), track_rect.top(), max(0.0, fill_x - track_rect.left()), track_rect.height())
        painter.setBrush(QColor(THEME["accent"]))
        painter.drawRoundedRect(fill_rect, 4.0, 4.0)

        painter.setBrush(QColor(THEME["accent_alt"] if self._dragging else THEME["accent"]))
        painter.drawEllipse(QPointF(fill_x, self.height() / 2), 7.0, 7.0)


class SubtitleLcdWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Raspberry Pi Subtitle LCD - PySide6")
        self.resize(LCD_WIDTH, LCD_HEIGHT)
        self.setMinimumSize(900, 560)
        self.setStyleSheet(self._build_stylesheet())

        self.last_total_time = 0.1
        self.last_display_text = ""
        self.last_source_text = ""
        self.subtitle_pages = [""]

        self._stop_event = threading.Event()
        self._bridge = UdpBridge()
        self._bridge.payload_received.connect(self.update_ui)

        self.sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_receive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_receive.bind(("0.0.0.0", MY_PORT))
        self.sock_receive.settimeout(0.5)

        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 위젯 구성과 수신 스레드를 초기화
        self._build_ui()
        self._receiver_thread = threading.Thread(target=self.receive_loop, daemon=True)
        self._receiver_thread.start()

        # 시작 직후 기본 안내 문구를 표시
        QTimer.singleShot(100, lambda: self.update_ui({"text": "대기 중...", "curr": 0, "total": 1}))

        if FULLSCREEN:
            self.showFullScreen()
        else:
            self.setGeometry(100, 100, LCD_WIDTH, LCD_HEIGHT)

    def _build_stylesheet(self) -> str:
        return f"""
        QMainWindow {{
            background-color: {THEME['bg']};
        }}
        QFrame#panelFrame {{
            background-color: rgba(255, 250, 245, 0.94);
            border: 1px solid rgba(181, 160, 147, 0.72);
            border-radius: 22px;
        }}
        QLabel {{
            color: {THEME['text']};
        }}
        QLabel#titleLabel {{
            color: {THEME['accent']};
            font-size: 18px;
            font-weight: 800;
        }}
        QLabel#languageLabel {{
            color: {THEME['accent']};
            font-size: 16px;
            font-weight: 800;
        }}
        QLabel#timeLabel {{
            color: {THEME['muted']};
            font-size: 14px;
            font-weight: 700;
        }}
        QTextEdit {{
            background-color: rgba(255, 255, 255, 0.84);
            color: {THEME['text']};
            border: 1px solid rgba(190, 205, 220, 0.85);
            border-radius: 14px;
            padding: 10px 12px;
        }}
        QTextEdit:focus {{
            border-color: rgba(233, 187, 172, 0.95);
            background-color: rgba(255, 255, 255, 0.96);
        }}
        QPushButton {{
            color: #fffaf7;
            background-color: {THEME['accent']};
            border: 1px solid rgba(110, 80, 68, 0.58);
            border-radius: 14px;
            padding: 16px 20px;
            font-weight: 800;
        }}
        QPushButton:hover {{
            background-color: #a97764;
            border-color: rgba(110, 80, 68, 0.76);
        }}
        QPushButton:pressed {{
            background-color: #7f5a4c;
        }}
        QPushButton#closeButton {{
            background-color: {THEME['accent_alt']};
        }}
        QPushButton#closeButton:hover {{
            background-color: #c7967b;
        }}
        QComboBox {{
            color: {THEME['text']};
            background-color: rgba(255, 255, 255, 0.88);
            border: 1px solid rgba(190, 205, 220, 0.85);
            border-radius: 14px;
            padding: 10px 12px;
            font-weight: 700;
        }}
        QComboBox:focus {{
            border-color: rgba(233, 187, 172, 0.95);
            background-color: rgba(255, 255, 255, 0.96);
        }}
        QComboBox::drop-down {{
            border: none;
            width: 26px;
        }}
        QComboBox QAbstractItemView {{
            background-color: rgba(255, 250, 245, 0.98);
            color: {THEME['text']};
            selection-background-color: rgba(208, 175, 155, 0.55);
            font-weight: 600;
        }}
        QProgressBar {{
            background-color: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(190, 205, 220, 0.80);
            border-radius: 8px;
            text-align: center;
            color: {THEME['text']};
            height: 18px;
        }}
        QProgressBar::chunk {{
            border-radius: 8px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 198, 183, 1), stop:1 rgba(190, 221, 255, 1));
        }}
        """

    # 메인 화면의 위젯 배치와 스타일을 구성
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 14, 20, 16)
        outer.setSpacing(8)

        top_frame = QFrame()
        top_frame.setObjectName("panelFrame")
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(16, 14, 16, 14)
        top_layout.setSpacing(12)

        self.lang_label = QLabel("언어:")
        self.lang_label.setFont(make_font(13, True))
        top_layout.addWidget(self.lang_label)

        self.language_combo = QComboBox()
        self.language_combo.setFont(make_font(13, True))
        self.language_combo.addItems([label for label, _ in LCD_LANGUAGE_OPTIONS])
        self.language_combo.currentTextChanged.connect(self.on_language_selected)
        top_layout.addWidget(self.language_combo, 0)

        top_layout.addStretch(1)

        self.close_button = QPushButton("종료")
        self.close_button.setObjectName("closeButton")
        self.close_button.setFont(make_font(13, True))
        self.close_button.clicked.connect(self.close)
        top_layout.addWidget(self.close_button)

        outer.addWidget(top_frame)

        self.title_label = QLabel("자막 대기 중...")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setFont(make_font(18, True))
        outer.addWidget(self.title_label)

        self.language_state_label = QLabel("자막: 원본")
        self.language_state_label.setObjectName("languageLabel")
        self.language_state_label.setFont(make_font(17, True))
        outer.addWidget(self.language_state_label)

        self.subtitle_text = QTextEdit()
        self.subtitle_text.setFont(make_font(34, True))
        self.subtitle_text.setReadOnly(True)
        self.subtitle_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self._set_centered_subtitle_text("PC 앱을 시작한 후 영상을 재생하세요.")
        outer.addWidget(self.subtitle_text, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        self.play_button = QPushButton("재생")
        self.play_button.setFont(make_font(17, True))
        self.play_button.setMinimumHeight(72)
        self.play_button.clicked.connect(self.send_play)
        button_row.addWidget(self.play_button)

        self.pause_button = QPushButton("정지")
        self.pause_button.setFont(make_font(17, True))
        self.pause_button.setMinimumHeight(72)
        self.pause_button.clicked.connect(self.send_pause)
        self.pause_button.setStyleSheet(f"background-color: {THEME['pause']};")
        button_row.addWidget(self.pause_button)

        self.save_button = QPushButton("단어 저장")
        self.save_button.setFont(make_font(17, True))
        self.save_button.setMinimumHeight(72)
        self.save_button.clicked.connect(self.send_save_word)
        self.save_button.setStyleSheet(f"background-color: {THEME['accent_alt']};")
        button_row.addWidget(self.save_button)

        outer.addLayout(button_row)

        bottom_frame = QFrame()
        bottom_frame.setObjectName("panelFrame")
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(14, 12, 14, 12)
        bottom_layout.setSpacing(14)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setFont(make_font(13, True))
        bottom_layout.addWidget(self.time_label, 0)

        self.seek_bar = SeekBar()
        self.seek_bar.seek_requested.connect(self.on_seek_requested)
        bottom_layout.addWidget(self.seek_bar, 1)

        outer.addWidget(bottom_frame)

    # PC로 제어 명령을 전송
    def send_command(self, message: str) -> None:
        try:
            self.sock_send.sendto(message.encode("utf-8"), (PC_IP, PC_PORT))
        except OSError:
            pass

    # 언어 변경 명령 전송
    def send_language(self, lang_code: str) -> None:
        self.send_command(f"SET_LANG:{lang_code}")

    # 재생 및 일시정지 명령 전송
    def send_play(self) -> None:
        self.send_command("CMD:PLAY")

    def send_pause(self) -> None:
        self.send_command("CMD:PAUSE")

    # 선택된 단어를 PC로 보내 단어장에 저장
    def send_save_word(self) -> None:
        selected_text = self.subtitle_text.textCursor().selectedText().replace("\u2029", " ").strip()
        if not selected_text:
            QMessageBox.warning(self, "단어 저장", "먼저 드래그로 단어를 선택해 주세요.")
            return

        result = QMessageBox.question(
            self,
            "단어 저장 확인",
            f"선택한 단어:\n\n{selected_text}\n\n이대로 저장하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.send_command(f"SAVE_WORD:{selected_text}")
            cursor = self.subtitle_text.textCursor()
            cursor.clearSelection()
            self.subtitle_text.setTextCursor(cursor)

    # 드롭다운에서 선택한 언어를 코드로 변환해 전송
    def on_language_selected(self, label: str) -> None:
        self.send_language(LCD_LANGUAGE_LABEL_TO_CODE.get(label, "original"))

    # 프로그레스 바 드래그 종료 시 탐색 위치 전송
    def on_seek_requested(self, target_time: float) -> None:
        self.send_command(f"SEEK:{target_time}")

    # 시간 표시 문자열을 포맷팅
    def _format_time(self, current: float, total: float) -> str:
        current_minutes, current_seconds = divmod(int(current), 60)
        total_minutes, total_seconds = divmod(int(total), 60)
        return f"{current_minutes}:{current_seconds:02d} / {total_minutes}:{total_seconds:02d}"

    # 자막 본문을 여러 줄에서도 가운데 정렬되도록 블록 단위로 갱신
    def _set_centered_subtitle_text(self, text: str) -> None:
        self.subtitle_text.setPlainText(text)
        cursor = self.subtitle_text.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cursor.mergeBlockFormat(block_format)
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.subtitle_text.setTextCursor(cursor)

    # 수신한 JSON 페이로드를 바탕으로 화면을 갱신
    def update_ui(self, payload) -> None:
        text = payload.get("text", "")
        overlay_text = payload.get("overlay_text", "")
        title = payload.get("title", "")
        lang_code = payload.get("lang", "original")
        curr = payload.get("curr", 0.0)
        total = payload.get("total", 0.1)
        cue_start = payload.get("cue_start")
        cue_end = payload.get("cue_end")

        if not isinstance(curr, (int, float)):
            curr = 0.0
        if not isinstance(total, (int, float)) or total <= 0:
            total = 0.1

        curr = max(0.0, min(float(curr), float(total)))
        self.last_total_time = float(total)

        if title and isinstance(title, str):
            self.title_label.setText(f"현재 재생: {title}")

        self.language_state_label.setText(f"자막: {get_language_label(lang_code)}")

        if isinstance(overlay_text, str) and overlay_text.strip():
            display_text = overlay_text.strip()
            self.subtitle_pages = [""]
            source_key = "__overlay__"
        else:
            if text != self.last_source_text:
                self.subtitle_pages = build_subtitle_pages(text)

            if self.subtitle_pages:
                page_count = max(1, len(self.subtitle_pages))
                page_index = 0
                if isinstance(cue_start, (int, float)) and isinstance(cue_end, (int, float)) and cue_end > cue_start:
                    cue_duration = max(0.0, float(cue_end) - float(cue_start))
                    elapsed = max(0.0, min(curr - float(cue_start), cue_duration))
                    per_page = max(cue_duration / page_count, 0.001)
                    page_index = min(int(elapsed // per_page), page_count - 1)
                display_text = self.subtitle_pages[page_index]
            else:
                display_text = ""
            source_key = text

        if source_key != self.last_source_text or display_text != self.last_display_text:
            self._set_centered_subtitle_text(display_text)
            self.last_source_text = source_key
            self.last_display_text = display_text

        if not self.seek_bar._dragging:
            self.time_label.setText(self._format_time(curr, total))
            self.seek_bar.set_time(curr, total)

    # 백그라운드에서 UDP 패킷을 수신
    def receive_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, _ = self.sock_receive.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            self._bridge.payload_received.emit(payload)

    # 종료 시 소켓과 루프를 정리
    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_event.set()
        try:
            self.sock_receive.close()
        except OSError:
            pass
        try:
            self.sock_send.close()
        except OSError:
            pass
        event.accept()


    # 애플리케이션을 시작하고 메인 창을 표시
def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Subtitle LCD")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(THEME["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(THEME["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(THEME["panel"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(THEME["accent"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#fffaf7"))
    app.setPalette(palette)
    app.setFont(make_font(12, False))

    window = SubtitleLcdWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

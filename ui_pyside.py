from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import winsound
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from db_service import DBService
from google_cloud_auth import get_google_credentials_path
from language_config import TRANSLATION_LANGUAGE_OPTIONS, VOCAB_TRANSLATION_OPTIONS, get_language_label, get_tts_language_code
from udp_service import UDPService

load_dotenv()

# 재작성된 UI에서 사용하는 통일 테마
THEME = {
    "bg": "#f8f1e8",
    "bg_alt": "#fcf8f3",
    "panel": "#fffaf7",
    "surface": "#e5cfbc",
    "accent": "#9b6d5b",
    "accent_alt": "#b8866f",
    "success": "#7f9f8d",
    "warning": "#c7a26f",
    "text": "#5a443a",
    "muted": "#8d776a",
    "border": "rgba(181, 160, 147, 0.54)",
}

FONTS = {
    "body": QFont("Malgun Gothic", 16),
    "title": QFont("Malgun Gothic", 26),
    "hero": QFont("Malgun Gothic", 36),
    "button": QFont("Malgun Gothic", 17),
    "section": QFont("Malgun Gothic", 20),
}

for font in FONTS.values():
    font.setWeight(QFont.Weight.DemiBold)


class _UiBridge(QObject):
    login_finished = Signal(object, object, object, str, bool, str)


class _TranslationBridge(QObject):
    finished = Signal(str)
    failed = Signal(str)


class _DetectionBridge(QObject):
    detected = Signal(str, str)


class _MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._app.quit()
        event.accept()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class _FramelessTitleBar(QFrame):
    def __init__(self, window: QWidget, title: str):
        super().__init__(window)
        self._window = window
        self._dragging = False
        self._drag_offset = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("windowTitleLabel")
        self.title_label.setFont(FONTS["section"])
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.minimize_button = self._make_control_button("─", self._window.showMinimized)
        self.maximize_button = self._make_control_button("⛶", self._toggle_maximize)
        self.close_button = self._make_control_button("✕", self._window.close, danger=True)

        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def _make_control_button(self, text: str, slot, danger: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("windowControlButtonDanger" if danger else "windowControlButton")
        button.setFixedSize(34, 28)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def _toggle_maximize(self) -> None:
        if self._window.isFullScreen() or self._window.isMaximized():
            self._window.showNormal()
            self.maximize_button.setText("⛶")
        else:
            self._window.showFullScreen()
            self.maximize_button.setText("❐")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            window_handle = self._window.windowHandle()
            if window_handle is not None and window_handle.startSystemMove():
                event.accept()
                return
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SubtitleUI:
    """기존 기능을 유지하면서 다시 구성한 PySide UI입니다.

    DB/UDP 동작, 로그인·회원가입, URL 감지, 자막 추출 시작, 단어장, TTS 재생을
    모두 유지하고, 시각 스타일만 더 부드러운 파스텔 톤으로 정리합니다.
    """

    def __init__(self, on_start_callback, get_state_callback):
        # 외부 콜백은 백그라운드 작업 시작과 현재 상태 조회에 사용된다.
        self.on_start_callback = on_start_callback
        self.get_state_callback = get_state_callback
        self._ui_bridge = _UiBridge()
        self._ui_bridge.login_finished.connect(self._finish_login_attempt)
        self._detection_bridge = _DetectionBridge()
        self._detection_bridge.detected.connect(self._apply_detected_url)
        self._intro_animations: list[QPropertyAnimation] = []

        key_path = get_google_credentials_path("GCP_VOCAB_CREDENTIALS_FILE")
        self.db = DBService(key_path)

        pi_ip = os.getenv("SUBTITLE_PI_IP", "127.0.0.1").strip() or "127.0.0.1"
        self.udp = UDPService(
            pi_ip=pi_ip,
            pi_port=_env_int("SUBTITLE_PI_PORT", 5005),
            command_port=_env_int("SUBTITLE_PC_COMMAND_PORT", 5006),
            get_state_callback=get_state_callback,
            on_save_word_callback=self._handle_save_word,
        )
        self.udp.start_listening()

        self.last_detected_url = ""
        self.last_detected_title = ""
        self.is_processing = False
        self.logged_in_user = None

        # QApplication과 테마를 먼저 준비한 뒤 메인 창을 구성한다.
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self._apply_theme()
        self.app.setFont(FONTS["body"])

        self.main_window = _MainWindow(self.app)
        self.main_window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.main_window.setWindowTitle("Vocalog Subtitle Hub")
        self.main_window.resize(1160, 730)

        # 메인 화면에서 재사용할 위젯 참조를 미리 보관한다.
        self.detected_url_edit: QLineEdit | None = None
        self.detected_title_label: QLabel | None = None
        self.url_edit: QLineEdit | None = None
        self.start_button: QPushButton | None = None
        self.status_label: QLabel | None = None
        self.progress_widget: QProgressBar | None = None
        self.current_title_label: QLabel | None = None
        self.current_url_label: QLabel | None = None

        self._build_ui()
        self.main_window.hide()
        self.show_login_dialog()

        threading.Thread(target=self.poll_detected_url, daemon=True).start()

        # UDP 송신 루프를 짧은 주기로 유지한다.
        self.udp_timer = QTimer(self.main_window)
        self.udp_timer.setInterval(100)
        self.udp_timer.timeout.connect(self.udp.send_loop_tick)
        self.udp_timer.start()

    def _apply_theme(self) -> None:
        # 메인 앱 전체에 동일한 파스텔 계열 테마를 적용한다.
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(THEME["bg"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(THEME["text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(THEME["panel"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(THEME["panel"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(THEME["text"]))
        self.app.setPalette(pal)

        self.app.setStyleSheet(
            f"""
            QWidget#appShell {{
                color: {THEME['text']};
                background-color: {THEME['bg']};
            }}
            QFrame#windowShell {{
                background-color: rgba(255, 250, 245, 0.98);
                border: 1px solid rgba(181, 160, 147, 0.72);
                border-radius: 22px;
            }}
            QFrame#windowTitleBar {{
                background-color: rgba(255, 250, 245, 0.92);
                border: none;
                border-radius: 16px;
            }}
            QLabel#windowTitleLabel {{
                color: {THEME['accent']};
                font-size: 20px;
                font-weight: 700;
            }}
            QFrame#glassCard, QFrame#heroCard, QFrame#statusCard {{
                background-color: rgba(255, 252, 248, 0.88);
                border: 1px solid {THEME['border']};
                border-radius: 24px;
            }}
            QLabel#heroTitle {{
                color: {THEME['accent']};
                font-size: 40px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QLabel#heroSubtitle {{
                color: {THEME['muted']};
                font-size: 16px;
            }}
            QLabel#detailWord {{
                color: {THEME['accent_alt']};
                font-size: 34px;
                font-weight: 700;
            }}
            QLabel#sectionTitle {{
                color: {THEME['text']};
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#sectionHint {{
                color: {THEME['muted']};
                font-size: 13px;
            }}
            QLabel#badge {{
                color: {THEME['text']};
                background-color: rgba(210, 235, 255, 0.65);
                border: 1px solid rgba(168, 202, 232, 0.55);
                border-radius: 999px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#badgeAccent {{
                color: {THEME['text']};
                background-color: rgba(255, 227, 217, 0.78);
                border: 1px solid rgba(233, 187, 172, 0.65);
                border-radius: 999px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton {{
                color: #fffaf7;
                background-color: #8f6656;
                border: 1px solid rgba(110, 80, 68, 0.58);
                border-radius: 14px;
                padding: 12px 18px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: #a97764;
                border-color: rgba(110, 80, 68, 0.76);
            }}
            QPushButton:pressed {{
                background-color: #7f5a4c;
            }}
            QPushButton#primaryButton {{
                background-color: #7f5a4c;
                color: #fffaf6;
                border: none;
            }}
            QPushButton#primaryButton:hover {{
                background-color: #906554;
            }}
            QPushButton#secondaryButton {{
                background-color: #a57a67;
                color: #fffaf6;
                border: 1px solid rgba(110, 80, 68, 0.58);
            }}
            QPushButton#secondaryButton:hover {{
                background-color: #b98a74;
            }}
            QPushButton#windowControlButton, QPushButton#windowControlButtonDanger {{
                background-color: rgba(255, 255, 255, 0.9);
                color: {THEME['text']};
                border: 1px solid rgba(181, 160, 147, 0.48);
                border-radius: 9px;
                padding: 0px;
                font-size: 18px;
                font-weight: 700;
            }}
            QPushButton#windowControlButton:hover {{
                background-color: rgba(240, 231, 225, 0.95);
            }}
            QPushButton#windowControlButtonDanger:hover {{
                background-color: rgba(245, 214, 212, 0.95);
                color: #8c4038;
            }}
            QTableWidget QScrollBar:vertical {{
                background: rgba(255, 245, 239, 0.88);
                width: 14px;
                margin: 2px;
                border-radius: 7px;
            }}
            QTableWidget QScrollBar::handle:vertical {{
                background: rgba(189, 152, 133, 0.72);
                min-height: 28px;
                border-radius: 6px;
            }}
            QTableWidget QScrollBar::add-line:vertical, QTableWidget QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
                border: none;
            }}
            QLineEdit, QTextEdit, QComboBox {{
                color: {THEME['text']};
                background-color: rgba(255, 255, 255, 0.84);
                border: 1px solid rgba(190, 205, 220, 0.85);
                border-radius: 14px;
                padding: 10px 12px;
                font-weight: 600;
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
                border-color: rgba(233, 187, 172, 0.95);
                background-color: rgba(255, 255, 255, 0.96);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 28px;
                background: transparent;
            }}
            QComboBox QAbstractItemView {{
                background-color: rgba(255, 249, 244, 0.98);
                color: {THEME['text']};
                selection-background-color: rgba(206, 173, 153, 0.55);
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
            QTableWidget {{
                color: {THEME['text']};
                background-color: rgba(255, 248, 242, 0.98);
                gridline-color: rgba(176, 148, 132, 0.20);
                selection-background-color: rgba(208, 175, 155, 0.55);
                selection-color: {THEME['text']};
                font-weight: 600;
            }}
            QHeaderView::section {{
                background-color: rgba(232, 210, 191, 0.95);
                color: {THEME['text']};
                padding: 8px;
                border: none;
                font-weight: 700;
            }}
            QComboBox QAbstractItemView {{
                background-color: rgba(255, 250, 245, 0.98);
                color: {THEME['text']};
                selection-background-color: rgba(208, 175, 155, 0.55);
                font-weight: 600;
            }}
            """
        )

    def _cleanup_theme_texture(self) -> None:
        return

    def _animate_widget_fade_in(self, widget: QWidget, delay_ms: int = 0, duration_ms: int = 420) -> None:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration_ms)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro_animations.append(animation)

        def start_animation() -> None:
            animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        if delay_ms:
            QTimer.singleShot(delay_ms, start_animation)
        else:
            start_animation()

    def _animate_main_window_show(self) -> None:
        self.main_window.setWindowOpacity(0.0)
        self.main_window.show()
        animation = QPropertyAnimation(self.main_window, b"windowOpacity", self.main_window)
        animation.setDuration(340)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro_animations.append(animation)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _attach_window_chrome(self, window: QWidget, layout: QVBoxLayout, title: str) -> None:
        title_bar = _FramelessTitleBar(window, title)
        layout.insertWidget(0, title_bar)

    def _make_window_shell(self, parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
        shell = QFrame(parent)
        shell.setObjectName("windowShell")
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(2, 2, 2, 2)
        shell_layout.setSpacing(0)
        return shell, shell_layout

    def _make_card(self, object_name: str, title_text: str, hint_text: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName(object_name)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(44, 36, 44, 36)
        layout.setSpacing(18)

        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        title.setFont(FONTS["section"])
        layout.addWidget(title)

        if hint_text:
            hint = QLabel(hint_text)
            hint.setObjectName("sectionHint")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        return frame, layout

    def _build_ui(self) -> None:
        # 로그인 이후에 보이는 메인 작업 화면을 구성한다.
        central = QWidget()
        central.setObjectName("appShell")
        self.main_window.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(0)

        shell, shell_layout = self._make_window_shell(central)
        root_layout.addWidget(shell)
        self._attach_window_chrome(self.main_window, shell_layout, "Vocalog Subtitle Hub")

        # 상단 영역: LCD IP 설정 전용 행을 타이틀 아래에 둬서 감지/상태 카드 비율에 영향 주지 않음
        top_frame = QFrame()
        top_frame.setObjectName("topControlFrame")
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(8)

        ip_label = QLabel("LCD IP:")
        ip_label.setFont(FONTS["body"])
        top_layout.addWidget(ip_label)

        self.pi_ip_edit = QLineEdit(self.udp.pi_ip)
        self.pi_ip_edit.setMaximumWidth(260)
        self.pi_ip_edit.setMinimumHeight(36)
        top_layout.addWidget(self.pi_ip_edit)

        set_ip_btn = QPushButton("설정")
        set_ip_btn.setMinimumHeight(36)
        set_ip_btn.clicked.connect(self._set_pi_ip)
        top_layout.addWidget(set_ip_btn)

        top_layout.addStretch(1)
        shell_layout.addWidget(top_frame)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setSpacing(14)
        content_layout.setContentsMargins(4, 0, 4, 0)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        detect_card, detect_layout = self._make_card("glassCard", "자동 감지")
        detect_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        detect_layout.setSpacing(24)
        self.detected_title_label = QLabel("제목 감지 대기 중...")
        self.detected_title_label.setFont(FONTS["section"])
        # 제목은 여러 줄로 감싸고, 세로 크기는 최소한만 늘린다.
        self.detected_title_label.setWordWrap(True)
        self.detected_title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.detected_title_label.setMaximumHeight(140)
        detect_layout.addWidget(self.detected_title_label)

        url_row = QHBoxLayout()
        self.detected_url_edit = QLineEdit("감지 대기 중...")
        self.detected_url_edit.setReadOnly(True)
        self.detected_url_edit.setMinimumHeight(54)
        use_btn = QPushButton("URL 사용")
        use_btn.setMinimumHeight(54)
        use_btn.clicked.connect(self.use_detected_url)
        url_row.addWidget(self.detected_url_edit)
        url_row.addWidget(use_btn)
        detect_layout.addLayout(url_row)

        manual_title = QLabel("수동 제어")
        manual_title.setObjectName("sectionTitle")
        manual_title.setFont(FONTS["section"])
        detect_layout.addWidget(manual_title)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("유튜브 URL을 입력하세요")
        self.url_edit.setMinimumHeight(54)
        detect_layout.addWidget(self.url_edit)

        detect_layout.addSpacing(10)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("추출 및 전송 시작")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumHeight(54)
        self.start_button.clicked.connect(self.start_processing)
        vocab_btn = QPushButton("내 단어장")
        vocab_btn.setMinimumHeight(54)
        vocab_btn.clicked.connect(self.open_vocab_window)
        action_row.addWidget(self.start_button)
        action_row.addWidget(vocab_btn)
        detect_layout.addLayout(action_row)
        right_panel = QVBoxLayout()
        right_panel.setSpacing(16)

        status_card, status_layout = self._make_card("statusCard", "실행 상태 / 현재 상태")
        status_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status_label = QLabel("대기 중...")
        self.status_label.setWordWrap(True)
        self.status_label.setFont(FONTS["section"])
        status_layout.addWidget(self.status_label)
        self.progress_widget = QProgressBar()
        self.progress_widget.setRange(0, 100)
        self.progress_widget.setValue(0)
        self.progress_widget.setFixedHeight(22)
        status_layout.addWidget(self.progress_widget)

        current_title = QLabel("현재 상태")
        current_title.setFont(FONTS["section"])
        status_layout.addWidget(current_title)

        self.current_title_label = QLabel("제목: -")
        self.current_url_label = QLabel("URL: -")
        # 현재 상태 문구는 감싸되, 카드 높이가 과하게 늘어나지 않게 한다.
        self.current_title_label.setWordWrap(True)
        self.current_title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.current_title_label.setMaximumHeight(80)
        # URL은 한 줄로 유지하고 긴 문자열은 가운데를 생략한다.
        self.current_url_label.setWordWrap(False)
        self.current_url_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.current_url_label.setMaximumHeight(40)
        status_layout.addWidget(self.current_title_label)
        status_layout.addWidget(self.current_url_label)

        status_card.setMinimumHeight(390)
        status_card.setMaximumHeight(390)
        detect_card.setMinimumHeight(390)
        detect_card.setMaximumHeight(390)
        right_panel.addWidget(status_card)
        content_layout.addWidget(detect_card, 6)
        content_layout.addLayout(right_panel, 4)
        # 감지 패널과 상태 패널의 폭 비율을 안정적으로 유지한다.
        content_layout.setStretch(0, 6)
        content_layout.setStretch(1, 4)
        shell_layout.addWidget(content)

        self._animate_widget_fade_in(detect_card, 40)
        self._animate_widget_fade_in(status_card, 180)

    # 다이얼로그
    def show_login_dialog(self) -> None:
        # 초기 진입 시 로그인 또는 회원가입을 선택하도록 한다.
        dialog = QDialog(None)
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        dialog.setWindowTitle("로그인")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(10, 10, 10, 10)
        dialog.setMinimumSize(640, 420)
        dialog.resize(680, 460)

        shell, shell_layout = self._make_window_shell(dialog)
        layout.addWidget(shell)
        shell_layout.setContentsMargins(12, 12, 12, 12)
        shell_layout.setSpacing(10)
        self._attach_window_chrome(dialog, shell_layout, "로그인")

        header_row = QHBoxLayout()
        monitor_icon = QLabel("📺")
        monitor_font = QFont("Malgun Gothic", 52)
        monitor_font.setWeight(QFont.Weight.DemiBold)
        monitor_icon.setFont(monitor_font)
        monitor_icon.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        icon = QLabel("🗒️")
        icon_font = QFont("Malgun Gothic", 48)
        icon_font.setWeight(QFont.Weight.DemiBold)
        icon.setFont(icon_font)
        icon.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("실시간 자막&단어장")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title.setFont(FONTS["title"])
        header_row.addWidget(monitor_icon)
        header_row.addWidget(title)
        header_row.addWidget(icon)
        shell_layout.addLayout(header_row)

        id_edit = QLineEdit()
        id_edit.setPlaceholderText("ID")
        id_edit.setMinimumHeight(46)
        shell_layout.addWidget(id_edit)

        pw_edit = QLineEdit()
        pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pw_edit.setPlaceholderText("Password")
        pw_edit.setMinimumHeight(46)
        shell_layout.addWidget(pw_edit)

        btn_row = QHBoxLayout()
        login_btn = QPushButton("로그인")
        signup_btn = QPushButton("회원가입")
        login_btn.setObjectName("primaryButton")
        signup_btn.setObjectName("secondaryButton")
        login_btn.setMinimumHeight(46)
        signup_btn.setMinimumHeight(46)
        btn_row.addWidget(login_btn)
        btn_row.addWidget(signup_btn)
        shell_layout.addLayout(btn_row)

        def attempt_login():
            user_id = id_edit.text().strip()
            pw = pw_edit.text().strip()
            if not user_id or not pw:
                QMessageBox.critical(dialog, "오류", "모두 입력해주세요.")
                return
            login_btn.setEnabled(False)
            signup_btn.setEnabled(False)

            def worker():
                try:
                    success, msg = self.db.login(user_id, pw)
                except Exception as exc:
                    success, msg = False, f"로그인 처리 중 오류가 발생했습니다: {exc}"

                self._ui_bridge.login_finished.emit(dialog, login_btn, signup_btn, user_id, success, msg)

            threading.Thread(target=worker, daemon=True).start()

        login_btn.clicked.connect(attempt_login)
        signup_btn.clicked.connect(lambda: self.show_signup_dialog(dialog))

        dialog.exec()

    def _finish_login_attempt(
        self,
        dialog: QDialog,
        login_btn: QPushButton,
        signup_btn: QPushButton,
        user_id: str,
        success: bool,
        msg: str,
    ) -> None:
        login_btn.setEnabled(True)
        signup_btn.setEnabled(True)
        if success:
            self.logged_in_user = user_id
            self.main_window.setWindowTitle(f"Vocalog Subtitle Hub - 로그인: {self.logged_in_user}")
            dialog.accept()
            self._animate_main_window_show()
        else:
            QMessageBox.critical(dialog, "오류", msg)

    def show_signup_dialog(self, parent: QDialog | QWidget) -> None:
        dialog = QDialog(None)
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        dialog.setWindowTitle("회원가입")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(10, 10, 10, 10)
        dialog.setMinimumSize(600, 360)
        dialog.resize(640, 400)

        shell, shell_layout = self._make_window_shell(dialog)
        layout.addWidget(shell)
        shell_layout.setContentsMargins(12, 12, 12, 12)
        shell_layout.setSpacing(10)
        self._attach_window_chrome(dialog, shell_layout, "회원가입")

        id_edit = QLineEdit()
        pw_edit = QLineEdit()
        email_edit = QLineEdit()
        id_edit.setPlaceholderText("ID")
        pw_edit.setPlaceholderText("Password")
        email_edit.setPlaceholderText("Email")
        for w in (id_edit, pw_edit, email_edit):
            w.setMinimumHeight(40)
            shell_layout.addWidget(w)

        btn = QPushButton("가입 완료")
        btn.clicked.connect(lambda: process_signup())
        shell_layout.addWidget(btn)

        def process_signup():
            user_id = id_edit.text().strip()
            pw = pw_edit.text().strip()
            email = email_edit.text().strip()
            if not user_id or not pw or not email:
                QMessageBox.critical(dialog, "오류", "모두 입력해주세요.")
                return
            try:
                success, msg = self.db.signup(user_id, pw, email)
            except Exception as exc:
                success, msg = False, f"회원가입 처리 중 오류가 발생했습니다: {exc}"
            if success:
                QMessageBox.information(dialog, "성공", msg)
                dialog.accept()
            else:
                QMessageBox.critical(dialog, "오류", msg)

        dialog.exec()

    def open_vocab_window(self) -> None:
        if not self.db.is_connected() or not self.logged_in_user:
            QMessageBox.critical(self.main_window, "오류", "로그인 상태가 아니거나 DB가 없습니다.")
            return

        dialog = QDialog(None)
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        dialog.setWindowTitle(f"내 단어장 - {self.logged_in_user}")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(10, 10, 10, 10)
        dialog.resize(620, 620)
        dialog.setMinimumSize(580, 560)

        shell, shell_layout = self._make_window_shell(dialog)
        layout.addWidget(shell)
        shell_layout.setContentsMargins(12, 12, 12, 12)
        shell_layout.setSpacing(8)
        self._attach_window_chrome(dialog, shell_layout, f"내 단어장 - {self.logged_in_user}")

        btn_row = QHBoxLayout()
        delete_btn = QPushButton("선택 삭제")
        delete_all_btn = QPushButton("전체 삭제")
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(delete_all_btn)
        btn_row.addStretch(1)
        shell_layout.addLayout(btn_row)

        guide_label = QLabel("단어를 더블클릭하면 상세 화면으로 이동합니다. 번역, 음성 듣기, 메모는 상세 화면에서 확인해 주세요.")
        guide_label.setWordWrap(True)
        guide_label.setFont(FONTS["body"])
        shell_layout.addWidget(guide_label)

        table = QTableWidget(0, 1)
        table.setHorizontalHeaderLabels(["저장된 단어"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setMinimumHeight(300)
        table.setFont(FONTS["body"])
        table.verticalHeader().setDefaultSectionSize(46)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        shell_layout.addWidget(table)

        vocab_cache: Dict[str, Dict[str, Any]] = {}

        def load_data():
            table.setRowCount(0)
            vocab_cache.clear()
            try:
                items = list(self.db.get_vocabulary(self.logged_in_user))
                table.setRowCount(len(items))
                for r, data in enumerate(items):
                    vocab_cache[data["id"]] = data
                    item = QTableWidgetItem(data.get("word", ""))
                    item.setData(Qt.ItemDataRole.UserRole, data["id"])
                    table.setItem(r, 0, item)
            except Exception as exc:
                QMessageBox.critical(dialog, "DB 로딩 실패", f"데이터를 가져오지 못했습니다: {exc}")

        def selected_ids() -> list[str]:
            ids: list[str] = []
            for idx in table.selectionModel().selectedRows():
                item = table.item(idx.row(), 0)
                if item:
                    doc_id = item.data(Qt.ItemDataRole.UserRole)
                    if doc_id:
                        ids.append(doc_id)
            return ids

        def delete_selected():
            ids = selected_ids()
            if not ids:
                QMessageBox.warning(dialog, "경고", "삭제할 단어를 선택해 주세요.")
                return
            reply = QMessageBox.question(dialog, "삭제 확인", f"선택한 {len(ids)}개의 단어를 삭제하시겠습니까?")
            if reply == QMessageBox.StandardButton.Yes:
                self.db.delete_vocabulary(ids)
                load_data()

        def delete_all():
            reply = QMessageBox.question(dialog, "전체 삭제", "내 단어장을 모두 비우시겠습니까?")
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    self.db.delete_all_vocabulary(self.logged_in_user)
                    load_data()
                except Exception as exc:
                    QMessageBox.critical(dialog, "오류", f"삭제 실패: {exc}")

        def open_detail():
            ids = selected_ids()
            if not ids:
                QMessageBox.warning(dialog, "경고", "상세히 볼 단어를 선택해 주세요.")
                return
            data = vocab_cache.get(ids[0])
            if not data:
                QMessageBox.critical(dialog, "오류", "선택한 단어 정보를 찾을 수 없습니다.")
                return
            self.show_detail_dialog(dialog, data, ids[0])

        def open_detail_from_index(index):
            item = table.item(index.row(), 0)
            if not item:
                return
            doc_id = item.data(Qt.ItemDataRole.UserRole)
            if not doc_id:
                return
            data = vocab_cache.get(doc_id)
            if data:
                self.show_detail_dialog(dialog, data, doc_id)

        delete_btn.clicked.connect(delete_selected)
        delete_all_btn.clicked.connect(delete_all)
        table.cellDoubleClicked.connect(lambda row, column: open_detail_from_index(table.model().index(row, column)))

        load_data()
        dialog.exec()

    def _set_pi_ip(self) -> None:
        ip = self.pi_ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self.main_window, "오류", "IP를 입력하세요.")
            return
        try:
            self.udp.set_pi_ip(ip)
            QMessageBox.information(self.main_window, "설정 완료", f"LCD IP를 {ip}로 설정했습니다.")
        except Exception as exc:
            QMessageBox.critical(self.main_window, "오류", f"IP 설정 중 오류가 발생했습니다: {exc}")

    def show_detail_dialog(self, parent: QDialog | QWidget, data: Dict[str, Any], doc_id: str) -> None:
        dialog = QDialog(None)
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        dialog.setWindowTitle(f"단어 상세 - {data.get('word', '')}")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(10, 10, 10, 10)
        dialog.resize(840, 640)
        dialog.setMinimumSize(780, 600)

        shell, shell_layout = self._make_window_shell(dialog)
        layout.addWidget(shell)
        shell_layout.setContentsMargins(12, 12, 12, 12)
        shell_layout.setSpacing(10)
        self._attach_window_chrome(dialog, shell_layout, f"단어 상세 - {data.get('word', '')}")

        word_label = QLabel(data.get("word", ""))
        word_label.setObjectName("detailWord")
        word_label.setFont(FONTS["hero"])
        shell_layout.addWidget(word_label)

        meta = QLabel(f"저장 언어: {get_language_label(data.get('lang_code') or data.get('lang', '알 수 없음'))}")
        meta.setFont(FONTS["section"])

        body = QGridLayout()
        body.setHorizontalSpacing(12)
        body.setVerticalSpacing(10)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)
        right_panel = QVBoxLayout()
        right_panel.setSpacing(8)

        box_height = 130

        original_box = QTextEdit()
        original_box.setReadOnly(True)
        original_box.setPlainText(data.get("word", ""))
        original_box.setMinimumHeight(box_height)
        original_box.setMaximumHeight(box_height)
        left_panel.addWidget(original_box)

        play_btn = QPushButton("원문 발음 듣기")
        play_btn.setMinimumHeight(50)
        play_btn.setMinimumWidth(220)
        play_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        detail_button_font = QFont("Malgun Gothic", 15)
        detail_button_font.setWeight(QFont.Weight.DemiBold)
        play_btn.setFont(detail_button_font)
        play_btn.setStyleSheet("padding: 10px 18px 11px 18px;")
        play_btn.clicked.connect(lambda: self._play_tts(data.get("word", ""), data.get("lang_code") or "original", dialog))
        left_panel.addWidget(play_btn)

        translated_play_btn = QPushButton("번역 발음 듣기")
        translated_play_btn.setMinimumHeight(50)
        translated_play_btn.setMinimumWidth(220)
        translated_play_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        translated_play_btn.setFont(detail_button_font)
        translated_play_btn.setStyleSheet("padding: 10px 18px 11px 18px;")

        translate_row = QHBoxLayout()
        translate_combo = QComboBox()
        translate_combo.addItems([label for label, _ in TRANSLATION_LANGUAGE_OPTIONS])
        translate_combo.setMinimumHeight(42)
        translate_combo.setMinimumWidth(170)
        translate_combo.setMinimumContentsLength(10)
        trans_btn = QPushButton("번역")
        trans_btn.setMinimumHeight(48)
        trans_btn.setMinimumWidth(96)
        trans_btn.setFont(detail_button_font)
        trans_btn.setStyleSheet("padding: 8px 16px 9px 16px;")
        translate_row.addWidget(translate_combo, 0)
        translate_row.addWidget(trans_btn, 0)

        translation_out = QTextEdit()
        translation_out.setReadOnly(True)
        translation_out.setText("번역 언어를 선택한 뒤 번역 버튼을 누르세요.")
        translation_out.setMinimumHeight(box_height)
        translation_out.setMaximumHeight(box_height)
        right_panel.addWidget(translation_out)

        right_panel.addWidget(translated_play_btn)

        header_row = QHBoxLayout()
        header_row.addWidget(meta)
        header_row.addStretch(1)
        header_row.addLayout(translate_row)
        shell_layout.addLayout(header_row)

        body.addLayout(left_panel, 1, 0)
        body.addLayout(right_panel, 1, 1)
        shell_layout.addLayout(body)

        def play_translated_pronunciation():
            translated_text = translation_out.toPlainText().strip()
            if not translated_text or translated_text.startswith("번역 언어") or translated_text.startswith("번역 중"):
                QMessageBox.warning(dialog, "경고", "먼저 번역을 실행해 주세요.")
                return
            target_lang_code = VOCAB_TRANSLATION_OPTIONS[translate_combo.currentText()]
            self._play_tts(translated_text, get_tts_language_code(target_lang_code, os.getenv("GCP_STT_LANGUAGE", "en-US")), dialog)

        translated_play_btn.clicked.connect(play_translated_pronunciation)

        memo_label = QLabel("메모")
        memo_label.setFont(FONTS["section"])
        shell_layout.addWidget(memo_label)

        memo = QTextEdit()
        memo.setText(data.get("note", ""))
        memo.setMinimumHeight(140)
        shell_layout.addWidget(memo)

        footer = QHBoxLayout()
        footer.addStretch(1)
        save_btn = QPushButton("메모 저장")
        save_btn.setMinimumHeight(46)
        save_btn.setMinimumWidth(140)
        save_btn.setFont(detail_button_font)
        save_btn.setStyleSheet("padding: 8px 18px 9px 18px;")
        footer.addWidget(save_btn)
        shell_layout.addLayout(footer)

        save_btn.clicked.connect(lambda: self._save_note(doc_id, memo.toPlainText(), dialog))

        def translate():
            target = VOCAB_TRANSLATION_OPTIONS[translate_combo.currentText()]
            translation_out.setText("번역 중...")

            translation_bridge = _TranslationBridge(dialog)
            dialog._translation_bridge = translation_bridge
            translation_bridge.finished.connect(lambda translated_text: translation_out.setText(translated_text))
            translation_bridge.failed.connect(lambda message: QMessageBox.critical(dialog, "번역 실패", message))

            def worker():
                try:
                    translated = self.db.translate_text(data.get("word", ""), target)
                    translation_bridge.finished.emit(translated)
                except Exception as exc:
                    translation_bridge.failed.emit(str(exc))

            threading.Thread(target=worker, daemon=True).start()

        trans_btn.clicked.connect(translate)

        dialog.exec()

    def _play_tts(self, text: str, lang_code: str, parent: QWidget) -> None:
        text = (text or "").strip()
        if not text:
            QMessageBox.warning(parent, "경고", "재생할 텍스트가 없습니다.")
            return

        def worker():
            try:
                audio_content = self.db.synthesize_speech(text, get_tts_language_code(lang_code, os.getenv("GCP_STT_LANGUAGE", "en-US")))
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
                    f.write(audio_content)
                    path = f.name
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception as exc:
                QTimer.singleShot(0, parent, lambda: QMessageBox.critical(parent, "TTS 실패", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_note(self, doc_id: str, note: str, parent: QWidget) -> None:
        def worker():
            try:
                self.db.update_vocabulary_note(doc_id, note.strip())
                QTimer.singleShot(0, parent, lambda: QMessageBox.information(parent, "완료", "메모가 저장되었습니다."))
            except Exception as exc:
                QTimer.singleShot(0, parent, lambda: QMessageBox.critical(parent, "저장 실패", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_save_word(self, word, lang_name, lang_code=None):
        # LCD에서 전달된 단어 저장 요청을 데이터베이스에 반영한다.
        if self.logged_in_user:
            self.db.translate_and_save_word(self.logged_in_user, word, lang_name, lang_code)

    def use_detected_url(self) -> None:
        detected = self.detected_url_edit.text().strip()
        if detected and not detected.startswith("감지"):
            self.url_edit.setText(detected)
            QTimer.singleShot(100, self.start_processing)

    def _apply_detected_url(self, url: str, title: str) -> None:
        if title and title != self.last_detected_title:
            self.last_detected_title = title
            if self.detected_title_label is not None:
                # 제목은 툴팁과 함께 전체를 보여 주도록 감싼다.
                self.detected_title_label.setText(title)
                self.detected_title_label.setToolTip(title)
            if self.current_title_label is not None:
                self.current_title_label.setText(f"제목: {title}")
                self.current_title_label.setToolTip(title)

        if url and url.startswith("http") and url != self.last_detected_url:
            self.last_detected_url = url
            if self.detected_url_edit is not None:
                self.detected_url_edit.setText(url)
            if self.current_url_label is not None:
                # URL은 가운데를 생략한 한 줄 표시로 바꾸고 전체 주소는 툴팁에 둔다.
                try:
                    fm = QFontMetrics(self.current_url_label.font())
                    max_w = self.current_url_label.width() or int(self.main_window.width() * 0.35)
                    elided = fm.elidedText(url, Qt.TextElideMode.ElideMiddle, max(40, max_w - 48))
                except Exception:
                    elided = url
                self.current_url_label.setText(f"URL: {elided}")
                self.current_url_label.setToolTip(url)

    def poll_detected_url(self) -> None:
        # Flask 서버에서 감지된 URL을 주기적으로 가져와 자동 입력란을 갱신한다.
        while True:
            try:
                res = requests.get("http://localhost:5000/get_detected_url", timeout=1.5)
                if res.ok:
                    data = res.json()
                    title = data.get("title")
                    url = data.get("url")
                    if title or url:
                        self._detection_bridge.detected.emit(url or "", title or "")
            except Exception:
                pass
            time.sleep(0.5)

    def start_processing(self) -> None:
        if self.is_processing:
            return
        url = self.url_edit.text().strip()
        if not url:
            return
        self.is_processing = True
        self.start_button.setEnabled(False)
        self.start_button.setText("처리 중...")
        self.status_label.setText("처리 시작...")
        self.progress_widget.setValue(0)
        self.status_label.setText("작업 진행 중...")
        self.udp.send_loop_tick()

        def update_progress(msg, percent):
            QTimer.singleShot(0, self.main_window, lambda: self.status_label.setText(msg))
            QTimer.singleShot(0, self.main_window, lambda: self.progress_widget.setValue(int(percent)))

        def on_complete(success=True, message=None):
            def finish():
                self.is_processing = False
                self.start_button.setEnabled(True)
                self.start_button.setText("추출 및 전송 시작")
                if success:
                    self.status_label.setText("전송 중... (파이 LCD를 확인하세요)")
                    if not self.udp_timer.isActive():
                        self.udp_timer.start()
                else:
                    self.status_label.setText(message or "자막 추출에 실패했습니다.")
                    QMessageBox.critical(self.main_window, "실패", message or "자막 추출에 실패했습니다.")
                self.udp.send_loop_tick()

            QTimer.singleShot(0, self.main_window, finish)

        self.on_start_callback(url, update_progress, on_complete)

    def run(self) -> None:
        self.app.exec()

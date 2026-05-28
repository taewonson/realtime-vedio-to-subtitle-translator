from __future__ import annotations

import atexit
import os
import sys
import tempfile
import threading
import time
import winsound
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db_service import DBService
from google_cloud_auth import get_google_credentials_path
from language_config import VOCAB_TRANSLATION_OPTIONS, get_tts_language_code
from udp_service import UDPService

load_dotenv()

# Minimal, consistent theme used by the rewritten UI
THEME = {
    "bg": "#07111f",
    "panel": "#0f1b2b",
    "surface": "#14273b",
    "accent": "#4fd1ff",
    "text": "#eef4ff",
}

FONTS = {
    "body": QFont("Malgun Gothic", 13),
    "title": QFont("Malgun Gothic", 24),
    "hero": QFont("Malgun Gothic", 32),
    "button": QFont("Malgun Gothic", 14),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class SubtitleUI:
    """Rewritten PySide UI that preserves app functionality.

    - Keeps DB and UDP behavior from original code.
    - Implements: login/signup, URL detect, start processing, vocab window, TTS play.
    - Applies a subtle tiled diagonal background for visual texture.
    """

    def __init__(self, on_start_callback, get_state_callback):
        self.on_start_callback = on_start_callback
        self.get_state_callback = get_state_callback

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
        self._theme_texture_path = None

        self.app = QApplication.instance() or QApplication(sys.argv)
        self._apply_theme()

        self.main_window = QMainWindow()
        self.main_window.setWindowTitle("Vocalog Neon Subtitle Hub")
        self.main_window.resize(1100, 860)

        # widgets
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

        # UDP periodic sender
        self.udp_timer = QTimer(self.main_window)
        self.udp_timer.setInterval(100)
        self.udp_timer.timeout.connect(self.udp.send_loop_tick)

    def _apply_theme(self) -> None:
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(THEME["bg"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(THEME["text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(THEME["panel"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(THEME["panel"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(THEME["text"]))
        self.app.setPalette(pal)

        # create a subtle diagonal tiled PNG and apply to an app-shell-like selector
        try:
            pix = QPixmap(80, 80)
            pix.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pix)
            pen = QPen(QColor(255, 255, 255, 10))
            pen.setWidth(1)
            painter.setPen(pen)
            for i in range(-80, 160, 16):
                painter.drawLine(i, 0, i + 80, 80)
            painter.end()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            pix.save(tmp.name, "PNG")
            tmp.close()
            self._theme_texture_path = tmp.name
            atexit.register(self._cleanup_theme_texture)
            # apply as stylesheet for widgets with objectName 'appShell'
            self.app.setStyleSheet(
                f"QWidget#appShell {{ background-color: {THEME['bg']}; background-image: url({tmp.name}); background-repeat: repeat; }}"
            )
        except Exception:
            pass

    def _cleanup_theme_texture(self) -> None:
        if self._theme_texture_path and os.path.exists(self._theme_texture_path):
            try:
                os.remove(self._theme_texture_path)
            except OSError:
                pass
            finally:
                self._theme_texture_path = None

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appShell")
        self.main_window.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.setSpacing(16)

        title = QLabel("Vocalog Neon Subtitle Hub")
        title.setFont(FONTS["hero"])
        title.setStyleSheet(f"color: {THEME['accent']};")
        root_layout.addWidget(title)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(14)
        right = QVBoxLayout()
        right.setSpacing(14)

        # Detect
        left.addWidget(QLabel("자동 감지"))
        self.detected_title_label = QLabel("제목 감지 대기 중...")
        self.detected_title_label.setFont(FONTS["body"])
        self.detected_title_label.setWordWrap(True)
        left.addWidget(self.detected_title_label)

        url_row = QHBoxLayout()
        self.detected_url_edit = QLineEdit("감지 대기 중...")
        self.detected_url_edit.setReadOnly(True)
        self.detected_url_edit.setMinimumHeight(48)
        use_btn = QPushButton("URL 사용")
        use_btn.setMinimumHeight(48)
        use_btn.clicked.connect(self.use_detected_url)
        url_row.addWidget(self.detected_url_edit)
        url_row.addWidget(use_btn)
        left.addLayout(url_row)

        # Manual
        left.addWidget(QLabel("수동 제어"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("유튜브 URL을 입력하세요")
        self.url_edit.setMinimumHeight(48)
        left.addWidget(self.url_edit)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("추출 및 전송 시작")
        self.start_button.setMinimumHeight(52)
        self.start_button.clicked.connect(self.start_processing)
        vocab_btn = QPushButton("내 단어장")
        vocab_btn.setMinimumHeight(52)
        vocab_btn.clicked.connect(self.open_vocab_window)
        action_row.addWidget(self.start_button)
        action_row.addWidget(vocab_btn)
        left.addLayout(action_row)

        # Right: status
        right.addWidget(QLabel("실행 상태"))
        self.status_label = QLabel("대기 중...")
        self.status_label.setWordWrap(True)
        right.addWidget(self.status_label)
        self.progress_widget = QProgressBar()
        self.progress_widget.setRange(0, 100)
        self.progress_widget.setValue(0)
        self.progress_widget.setFixedHeight(16)
        right.addWidget(self.progress_widget)

        right.addWidget(QLabel("현재 상태"))
        self.current_title_label = QLabel("제목: -")
        self.current_url_label = QLabel("URL: -")
        right.addWidget(self.current_title_label)
        right.addWidget(self.current_url_label)

        content_layout.addLayout(left, 3)
        content_layout.addLayout(right, 2)
        root_layout.addWidget(content)

    # Dialogs
    def show_login_dialog(self) -> None:
        dialog = QDialog(self.main_window)
        dialog.setWindowTitle("로그인")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("자막 엔진 로그인")
        title.setFont(FONTS["title"])
        layout.addWidget(title)

        id_edit = QLineEdit()
        id_edit.setPlaceholderText("ID")
        id_edit.setMinimumHeight(42)
        layout.addWidget(id_edit)

        pw_edit = QLineEdit()
        pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pw_edit.setPlaceholderText("Password")
        pw_edit.setMinimumHeight(42)
        layout.addWidget(pw_edit)

        btn_row = QHBoxLayout()
        login_btn = QPushButton("로그인")
        signup_btn = QPushButton("회원가입")
        login_btn.setMinimumHeight(44)
        signup_btn.setMinimumHeight(44)
        btn_row.addWidget(login_btn)
        btn_row.addWidget(signup_btn)
        layout.addLayout(btn_row)

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

                def finish():
                    login_btn.setEnabled(True)
                    signup_btn.setEnabled(True)
                    if success:
                        self.logged_in_user = user_id
                        self.main_window.setWindowTitle(f"Vocalog Subtitle Hub - 로그인: {self.logged_in_user}")
                        dialog.accept()
                        self.main_window.show()
                    else:
                        QMessageBox.critical(dialog, "오류", msg)

                QTimer.singleShot(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        login_btn.clicked.connect(attempt_login)
        signup_btn.clicked.connect(lambda: self.show_signup_dialog(dialog))

        dialog.exec()

    def show_signup_dialog(self, parent: QDialog | QWidget) -> None:
        dialog = QDialog(parent)
        dialog.setWindowTitle("회원가입")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)

        id_edit = QLineEdit()
        pw_edit = QLineEdit()
        email_edit = QLineEdit()
        id_edit.setPlaceholderText("ID")
        pw_edit.setPlaceholderText("Password")
        email_edit.setPlaceholderText("Email")
        for w in (id_edit, pw_edit, email_edit):
            w.setMinimumHeight(40)
            layout.addWidget(w)

        btn = QPushButton("가입 완료")
        btn.clicked.connect(lambda: process_signup())
        layout.addWidget(btn)

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

        dialog = QDialog(self.main_window)
        dialog.setWindowTitle(f"내 단어장 - {self.logged_in_user}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)

        btn_row = QHBoxLayout()
        delete_btn = QPushButton("선택 삭제")
        delete_all_btn = QPushButton("전체 삭제")
        detail_btn = QPushButton("상세 보기")
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(delete_all_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(detail_btn)
        layout.addLayout(btn_row)

        table = QTableWidget(0, 1)
        table.setHorizontalHeaderLabels(["저장된 단어"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setMinimumHeight(420)
        table.setFont(FONTS["body"])
        table.verticalHeader().setDefaultSectionSize(46)
        layout.addWidget(table)

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

        delete_btn.clicked.connect(delete_selected)
        delete_all_btn.clicked.connect(delete_all)
        detail_btn.clicked.connect(open_detail)

        load_data()
        dialog.exec()

    def show_detail_dialog(self, parent: QDialog | QWidget, data: Dict[str, Any], doc_id: str) -> None:
        dialog = QDialog(parent)
        dialog.setWindowTitle(f"단어 상세 - {data.get('word', '')}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)

        word_label = QLabel(data.get("word", ""))
        word_label.setFont(FONTS["hero"])
        layout.addWidget(word_label)

        meta = QLabel(f"저장 언어: {data.get('lang', '알 수 없음')}")
        layout.addWidget(meta)

        play_btn = QPushButton("원본 발음 듣기")
        play_btn.clicked.connect(lambda: self._play_tts(data.get("word", ""), data.get("lang_code") or "original", dialog))
        layout.addWidget(play_btn)

        translate_combo = QComboBox()
        translate_combo.addItems(list(VOCAB_TRANSLATION_OPTIONS.keys()))
        layout.addWidget(translate_combo)

        translation_out = QTextEdit()
        translation_out.setReadOnly(True)
        translation_out.setText("번역 언어를 선택한 뒤 번역 버튼을 누르세요.")
        translation_out.setMinimumHeight(110)
        layout.addWidget(translation_out)

        memo = QTextEdit()
        memo.setText(data.get("note", ""))
        memo.setMinimumHeight(160)
        layout.addWidget(memo)

        save_btn = QPushButton("메모 저장")
        save_btn.clicked.connect(lambda: self._save_note(doc_id, memo.toPlainText(), dialog))
        layout.addWidget(save_btn)

        def translate():
            target = VOCAB_TRANSLATION_OPTIONS[translate_combo.currentText()]
            translation_out.setText("번역 중...")

            def worker():
                try:
                    translated = self.db.translate_text(data.get("word", ""), target)
                    QTimer.singleShot(0, lambda: translation_out.setText(translated))
                except Exception as exc:
                    QTimer.singleShot(0, lambda: QMessageBox.critical(dialog, "번역 실패", str(exc)))

            threading.Thread(target=worker, daemon=True).start()

        trans_btn = QPushButton("번역")
        trans_btn.clicked.connect(translate)
        layout.addWidget(trans_btn)

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
                QTimer.singleShot(0, lambda: QMessageBox.critical(parent, "TTS 실패", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_note(self, doc_id: str, note: str, parent: QWidget) -> None:
        def worker():
            try:
                self.db.update_vocabulary_note(doc_id, note.strip())
                QTimer.singleShot(0, lambda: QMessageBox.information(parent, "완료", "메모가 저장되었습니다."))
            except Exception as exc:
                QTimer.singleShot(0, lambda: QMessageBox.critical(parent, "저장 실패", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_save_word(self, word, lang_name, lang_code=None):
        if self.logged_in_user:
            self.db.translate_and_save_word(self.logged_in_user, word, lang_name, lang_code)

    def use_detected_url(self) -> None:
        detected = self.detected_url_edit.text().strip()
        if detected and not detected.startswith("감지"):
            self.url_edit.setText(detected)
            QTimer.singleShot(100, self.start_processing)

    def poll_detected_url(self) -> None:
        while True:
            try:
                res = requests.get("http://localhost:5000/get_detected_url", timeout=1.5)
                if res.ok:
                    data = res.json()
                    title = data.get("title")
                    url = data.get("url")
                    if title and title != self.last_detected_title:
                        self.last_detected_title = title
                        QTimer.singleShot(0, lambda t=title: self.detected_title_label.setText(t))
                        QTimer.singleShot(0, lambda t=title: self.current_title_label.setText(f"제목: {t}"))
                    if url and url.startswith("http") and url != self.last_detected_url:
                        self.last_detected_url = url
                        QTimer.singleShot(0, lambda u=url: self.detected_url_edit.setText(u))
                        QTimer.singleShot(0, lambda u=url: self.current_url_label.setText(f"URL: {u}"))
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

        def update_progress(msg, percent):
            QTimer.singleShot(0, lambda: self.status_label.setText(msg))
            QTimer.singleShot(0, lambda: self.progress_widget.setValue(int(percent)))

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

            QTimer.singleShot(0, finish)

        self.on_start_callback(url, update_progress, on_complete)

    def run(self) -> None:
        self.app.exec()

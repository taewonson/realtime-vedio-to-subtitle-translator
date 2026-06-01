# PC와 라즈베리파이 LCD 사이의 자막 데이터 전송과 제어 명령 수신을 UDP로 처리합니다.
"""UDP bridge between the PC subtitle engine and LCD client."""

import socket
import json
import threading
from flask_server import state
from language_config import get_language_label


# ==========================================
# LCD 전송 및 명령 수신을 담당하는 UDP 브리지
# ==========================================
class UDPService:
    def __init__(self, pi_ip, pi_port, command_port, get_state_callback, on_save_word_callback):
        self.pi_ip = pi_ip
        self.pi_port = pi_port
        self.command_port = command_port
        self.get_state_callback = get_state_callback
        self.on_save_word_callback = on_save_word_callback
        
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.bind(("0.0.0.0", self.command_port))
        
        self.current_lang = "original"

    # 제어 명령 수신 루프를 별도 스레드로 시작
    def start_listening(self):
        threading.Thread(target=self._listen_for_commands, daemon=True).start()

    # 현재 공유 상태를 기반으로 LCD로 보낼 JSON payload를 구성
    def send_loop_tick(self):
        current_state = self.get_state_callback()
        payload_dict = self._build_payload(current_state)
        payload_str = json.dumps(payload_dict)
        
        try:
            self.sock_send.sendto(payload_str.encode('utf-8'), (self.pi_ip, self.pi_port))
        except OSError:
            pass

    def _build_payload(self, current_state):
        current_texts = current_state.get('texts', {})
        playback_mismatch = bool(current_state.get('playback_mismatch'))
        processing = bool(current_state.get('processing'))

        display_text = ""
        if not playback_mismatch:
            display_text = current_texts.get(self.current_lang, "") if isinstance(current_texts, dict) else ""

        overlay_text = "다른 영상 재생 중" if playback_mismatch else ("작업 진행 중..." if processing and not display_text else "")

        return {
            "text": display_text,
            "curr": current_state.get('curr', 0.0),
            "total": current_state.get('total', 0.1),
            "title": current_state.get('title', ""),
            "lang": self.current_lang,
            "cue_start": current_state.get('cue_start'),
            "cue_end": current_state.get('cue_end'),
            "overlay_text": overlay_text,
            "processing": processing,
        }

    # LCD에서 들어오는 재생/탐색/언어/단어저장 명령을 처리
    def _listen_for_commands(self):
        while True:
            try:
                data, _ = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')

                command, separator, value = msg.partition(":")

                if command == "SET_LANG" and separator:
                    self.current_lang = value
                elif command == "SEEK" and separator:
                    state.pending_command = {"command": "seek", "time": float(value)}
                elif msg == "CMD:PLAY":
                    state.pending_command = {"command": "play"}
                elif msg == "CMD:PAUSE":
                    state.pending_command = {"command": "pause"}
                elif command == "SAVE_WORD" and separator:
                    word = value.strip()
                    lang_name = get_language_label(self.current_lang)

                    if self.on_save_word_callback:
                        threading.Thread(target=self.on_save_word_callback, args=(word, lang_name, self.current_lang), daemon=True).start()
            except (OSError, UnicodeDecodeError, ValueError):
                pass

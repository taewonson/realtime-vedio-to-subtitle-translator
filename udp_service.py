# PC와 라즈베리파이 LCD 사이의 자막 데이터 전송과 제어 명령 수신을 UDP로 처리합니다.
"""UDP bridge between the PC subtitle engine and LCD client."""

import socket
import json
import threading
from flask_server import state
from language_config import get_language_label

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
        self.last_sent_payload = ""

    def start_listening(self):
        threading.Thread(target=self._listen_for_commands, daemon=True).start()

    def send_loop_tick(self):
        current_state = self.get_state_callback()
        current_texts = current_state.get('texts', {})
        playback_mismatch = bool(current_state.get('playback_mismatch'))
        
        display_text = current_texts[self.current_lang] if not playback_mismatch and current_texts and self.current_lang in current_texts else ""
        overlay_text = "다른 영상 재생 중" if playback_mismatch else ""
            
        payload_dict = {
            "text": display_text,
            "curr": current_state.get('curr', 0.0),
            "total": current_state.get('total', 0.1),
            "title": current_state.get('title', ""),
            "lang": self.current_lang,
            "cue_start": current_state.get('cue_start'),
            "cue_end": current_state.get('cue_end'),
            "overlay_text": overlay_text,
        }
        payload_str = json.dumps(payload_dict)
        
        if payload_str != self.last_sent_payload:
            try:
                self.sock_send.sendto(payload_str.encode('utf-8'), (self.pi_ip, self.pi_port))
                self.last_sent_payload = payload_str
            except OSError:
                pass

    def _listen_for_commands(self):
        while True:
            try:
                data, _ = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')

                if msg.startswith("SET_LANG:"):
                    self.current_lang = msg.split(":")[1]
                elif msg.startswith("SEEK:"):
                    state.pending_command = {"command": "seek", "time": float(msg.split(":")[1])}
                elif msg == "CMD:PLAY":
                    state.pending_command = {"command": "play"}
                elif msg == "CMD:PAUSE":
                    state.pending_command = {"command": "pause"}
                elif msg.startswith("SAVE_WORD:"):
                    word = msg.split(":", 1)[1].strip()
                    lang_name = get_language_label(self.current_lang)

                    if self.on_save_word_callback:
                        threading.Thread(target=self.on_save_word_callback, args=(word, lang_name, self.current_lang), daemon=True).start()
            except (OSError, UnicodeDecodeError, ValueError):
                pass

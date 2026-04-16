import tkinter as tk
from tkinter import ttk
import socket
import threading
import json
import requests
import time

from flask_server import state

class SubtitleUI:
    def __init__(self, on_start_callback, get_state_callback):
        self.on_start_callback = on_start_callback
        self.get_state_callback = get_state_callback
        
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.bind(("0.0.0.0", 5006))
        
        self.pi_ip = "127.0.0.1"
        self.pi_port = 5005
        self.current_lang = "original"
        self.last_sent_payload = ""
        self.last_detected_url = ""
        self.last_detected_title = ""
        
        self.root = tk.Tk()
        self.root.title("PC 자막 엔진 (URL 관리자)")
        self.root.geometry("700x450")
        
        self.setup_frame = tk.Frame(self.root)
        self.setup_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 💡 크롬 확장이 감지한 현재 URL 표시 영역 (새로 추가)
        detect_frame = tk.LabelFrame(self.setup_frame, text="🔗 현재 시청 중인 유튜브 (자동 감지)", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        detect_frame.pack(fill='x', pady=10)

        self.detected_title_var = tk.StringVar(value="제목 감지 대기 중...")
        self.detected_title_label = tk.Label(detect_frame, textvariable=self.detected_title_var, font=("맑은 고딕", 10, "bold"), anchor='w')
        self.detected_title_label.pack(fill='x', padx=5, pady=(0, 6))
        
        self.detected_url_var = tk.StringVar(value="감지 대기 중...")
        self.detected_url_label = tk.Entry(detect_frame, textvariable=self.detected_url_var, width=70, font=("맑은 고딕", 9), state='readonly')
        self.detected_url_label.pack(fill='x', side='left', expand=True, padx=5)
        
        self.use_detected_btn = tk.Button(detect_frame, text="사용", font=("맑은 고딕", 10, "bold"), bg="#2196F3", fg="white", command=self.use_detected_url, padx=15, pady=5)
        self.use_detected_btn.pack(side='left', padx=5)
        
        # 💡 수동 입력 URL 영역 (기존)
        manual_frame = tk.LabelFrame(self.setup_frame, text="📝 수동 URL 입력", font=("맑은 고딕", 10, "bold"), padx=10, pady=10)
        manual_frame.pack(fill='x', pady=10)
        
        self.url_entry = tk.Entry(manual_frame, width=80, font=("맑은 고딕", 10))
        self.url_entry.pack(fill='x', pady=10)
        
        self.start_btn = tk.Button(manual_frame, text="추출 및 전송 시작", font=("맑은 고딕", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.start_btn.pack(pady=5)
        
        self.status_label = tk.Label(self.setup_frame, text="대기 중...", font=("맑은 고딕", 10), fg="gray")
        self.status_label.pack(pady=10)
        
        self.progress = ttk.Progressbar(self.setup_frame, orient="horizontal", length=600, mode="determinate")
        self.progress.pack()

        threading.Thread(target=self.listen_for_commands, daemon=True).start()
        threading.Thread(target=self.poll_detected_url, daemon=True).start()  # 💡 크롬 확장의 URL을 폴링

    def use_detected_url(self):
        """감지된 URL을 수동 입력창에 자동 채우고 바로 처리"""
        detected = self.detected_url_var.get().strip()
        if detected and not detected.startswith("감지"):
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, detected)
            self.root.after(100, self.start_processing)  # 자동으로 처리 시작

    def poll_detected_url(self):
        """Poll Flask server for detected YouTube URL from Chrome extension"""
        while True:
            try:
                response = requests.get('http://localhost:5000/get_detected_url', timeout=1.5)
                if response.ok:
                    data = response.json()
                    detected_url = data.get('url')
                    detected_title = data.get('title')
                    if detected_title and isinstance(detected_title, str) and detected_title != self.last_detected_title:
                        self.last_detected_title = detected_title
                        self.root.after(0, lambda t=detected_title: self.detected_title_var.set(t))
                    if detected_url and detected_url.startswith('http') and detected_url != self.last_detected_url:
                        self.last_detected_url = detected_url
                        self.root.after(0, lambda u=detected_url: self.detected_url_var.set(u))
            except (requests.exceptions.RequestException, ValueError):
                pass
            time.sleep(0.5)

    def start_processing(self):
        url = self.url_entry.get().strip()
        if not url: return
        self.start_btn.config(state='disabled', text="처리 중...")
        self.progress["value"] = 0
        
        def update_progress(msg, percent):
            self.root.after(0, lambda: self.status_label.config(text=msg))
            self.root.after(0, lambda: self.progress.configure(value=percent))
            
        def on_complete():
            self.root.after(0, lambda: self.start_btn.config(state='normal', text="새 URL 추출 시작"))
            self.root.after(0, lambda: self.status_label.config(text="✅ 전송 중... (파이 LCD를 확인하세요)"))
            if not hasattr(self, 'loop_running'):
                self.loop_running = True
                self.send_loop()
            
        self.on_start_callback(url, update_progress, on_complete)

    def send_loop(self):
        current_state = self.get_state_callback()
        current_texts = current_state.get('texts', {})
        playback_mismatch = bool(current_state.get('playback_mismatch'))
        
        display_text = ""
        if (not playback_mismatch) and current_texts and self.current_lang in current_texts:
            display_text = current_texts[self.current_lang]

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
        
        self.root.after(100, self.send_loop)

    def listen_for_commands(self):
        while True:
            try:
                data, _ = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')

                if msg.startswith("SET_LANG:"):
                    self.current_lang = msg.split(":")[1]
                elif msg.startswith("SEEK:"):
                    seek_time = float(msg.split(":")[1])
                    state.pending_command = {"command": "seek", "time": seek_time}
                elif msg == "CMD:PLAY":
                    state.pending_command = {"command": "play"}
                elif msg == "CMD:PAUSE":
                    state.pending_command = {"command": "pause"}
            except (OSError, UnicodeDecodeError, ValueError):
                pass

    def run(self):
        self.root.mainloop()
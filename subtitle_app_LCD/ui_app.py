import tkinter as tk
from tkinter import ttk
import socket
import threading
import json

class SubtitleUI:
    def __init__(self, on_start_callback, get_state_callback):
        self.on_start_callback = on_start_callback
        self.get_state_callback = get_state_callback
        
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.bind(("0.0.0.0", 5006))
        
        self.pi_ip = "127.0.0.1"
        self.pi_port = 5005
        self.current_lang = "ko"
        self.last_sent_payload = "" # 중복 전송 방지용 
        
        self.root = tk.Tk()
        self.root.title("PC 자막 엔진 (URL 관리자)")
        self.root.geometry("600x300")
        
        self.setup_frame = tk.Frame(self.root)
        self.setup_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        tk.Label(self.setup_frame, text="유튜브 URL 입력:", font=("맑은 고딕", 11, "bold")).pack(anchor='w')
        self.url_entry = tk.Entry(self.setup_frame, width=60, font=("맑은 고딕", 11))
        self.url_entry.pack(fill='x', pady=10)
        
        self.start_btn = tk.Button(self.setup_frame, text="추출 및 전송 시작", font=("맑은 고딕", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.start_btn.pack(pady=5)
        
        self.status_label = tk.Label(self.setup_frame, text="대기 중...", font=("맑은 고딕", 10), fg="gray")
        self.status_label.pack(pady=10)
        
        self.progress = ttk.Progressbar(self.setup_frame, orient="horizontal", length=500, mode="determinate")
        self.progress.pack()

        threading.Thread(target=self.listen_for_commands, daemon=True).start()

    def listen_for_commands(self):
        while True:
            try:
                data, addr = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')
                if msg.startswith("SET_LANG:"):
                    self.current_lang = msg.split(":")[1]
            except:
                pass

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
        # 💡 시간과 텍스트 정보를 모두 가져옴
        state = self.get_state_callback()
        current_texts = state['texts']
        
        display_text = ""
        if current_texts and self.current_lang in current_texts:
            display_text = current_texts[self.current_lang]
            
        # JSON 형태로 패키징
        payload_dict = {
            "text": display_text,
            "curr": state['curr'],
            "total": state['total']
        }
        payload_str = json.dumps(payload_dict)
        
        # 데이터가 바뀌었을 때 (시간이 흘러도 바뀜) 파이로 전송
        if payload_str != self.last_sent_payload:
            try:
                self.sock_send.sendto(payload_str.encode('utf-8'), (self.pi_ip, self.pi_port))
                self.last_sent_payload = payload_str
            except:
                pass
        
        self.root.after(100, self.send_loop)

    def listen_for_commands(self):
            while True:
                try:
                    data, addr = self.sock_recv.recvfrom(1024)
                    msg = data.decode('utf-8')
                    if msg.startswith("SET_LANG:"):
                        self.current_lang = msg.split(":")[1]
                    # 💡 시간 이동 명령 추가
                    elif msg.startswith("SEEK:"):
                        seek_time = float(msg.split(":")[1])
                        from flask_server import state
                        state.pending_command = {"command": "seek", "time": seek_time}
                except:
                    pass

    def listen_for_commands(self):
        while True:
            try:
                data, addr = self.sock_recv.recvfrom(1024)
                msg = data.decode('utf-8')
                
                from flask_server import state # 상태 저장소 불러오기
                
                if msg.startswith("SET_LANG:"):
                    self.current_lang = msg.split(":")[1]
                elif msg.startswith("SEEK:"):
                    seek_time = float(msg.split(":")[1])
                    state.pending_command = {"command": "seek", "time": seek_time}
                # 💡 재생, 정지, 볼륨 명령 추가
                elif msg == "CMD:PLAY":
                    state.pending_command = {"command": "play"}
                elif msg == "CMD:PAUSE":
                    state.pending_command = {"command": "pause"}
                elif msg.startswith("VOL:"):
                    vol_val = int(msg.split(":")[1])
                    state.pending_command = {"command": "volume", "value": vol_val}
            except:
                pass

    def run(self):
        self.root.mainloop()

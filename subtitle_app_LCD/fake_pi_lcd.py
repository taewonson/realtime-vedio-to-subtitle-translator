import socket
import tkinter as tk
from tkinter import ttk
import threading
import json

# 📡 통신 설정
PC_IP = "127.0.0.1"    
MY_PORT = 5005         
PC_PORT = 5006         

sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receive.bind(("0.0.0.0", MY_PORT))
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

last_total_time = 0.1  
is_dragging = False 

# === 💡 전송 명령 함수들 ===
def send_language(lang_code):
    sock_send.sendto(f"SET_LANG:{lang_code}".encode('utf-8'), (PC_IP, PC_PORT))

def send_play():
    sock_send.sendto("CMD:PLAY".encode('utf-8'), (PC_IP, PC_PORT))

def send_pause():
    sock_send.sendto("CMD:PAUSE".encode('utf-8'), (PC_IP, PC_PORT))

def send_volume(val):
    # Scale 위젯은 소수점으로 값을 주므로 int로 변환
    vol = int(float(val))
    sock_send.sendto(f"VOL:{vol}".encode('utf-8'), (PC_IP, PC_PORT))

# === 진행바 드래그 로직 ===
def on_press(event):
    global is_dragging
    is_dragging = True
    update_drag_ui(event)

def on_drag(event):
    if is_dragging:
        update_drag_ui(event)

def on_release(event):
    global is_dragging, last_total_time
    is_dragging = False
    canvas_w = canvas.winfo_width()
    if canvas_w > 0:
        click_x = max(0, min(event.x, canvas_w))
        target_time = (click_x / canvas_w) * last_total_time
        sock_send.sendto(f"SEEK:{target_time}".encode('utf-8'), (PC_IP, PC_PORT))

def update_drag_ui(event):
    global last_total_time
    canvas_w = canvas.winfo_width()
    if canvas_w > 0:
        click_x = max(0, min(event.x, canvas_w))
        temp_time = (click_x / canvas_w) * last_total_time
        
        curr_m, curr_s = divmod(int(temp_time), 60)
        tot_m, tot_s = divmod(int(last_total_time), 60)
        time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")
        
        canvas.delete("all")
        canvas.create_rectangle(0, 8, canvas_w, 12, fill="#555555", outline="")
        canvas.create_rectangle(0, 8, click_x, 12, fill="#FF0000", outline="")
        canvas.create_oval(click_x - 6, 4, click_x + 6, 16, fill="#FF0000", outline="")

# === 데이터 수신 및 화면 갱신 ===
def update_ui(payload):
    global last_total_time, is_dragging
    text = payload.get("text", "")
    curr = payload.get("curr", 0.0)
    total = payload.get("total", 0.1)
    last_total_time = total 

    lcd_label.config(text=text)

    if not is_dragging:
        curr_m, curr_s = divmod(int(curr), 60)
        tot_m, tot_s = divmod(int(total), 60)
        time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

        canvas_w = canvas.winfo_width()
        if canvas_w < 10: canvas_w = 600
        canvas.delete("all")
        canvas.create_rectangle(0, 8, canvas_w, 12, fill="#555555", outline="")
        fill_w = (curr / total) * canvas_w
        canvas.create_rectangle(0, 8, fill_w, 12, fill="#FF0000", outline="")
        canvas.create_oval(fill_w - 6, 4, fill_w + 6, 16, fill="#FF0000", outline="")

def receive_loop():
    while True:
        try:
            data, addr = sock_receive.recvfrom(2048)
            payload = json.loads(data.decode('utf-8'))
            root.after(0, lambda p=payload: update_ui(p))
        except:
            pass

# === GUI 구성 ===
root = tk.Tk()
root.title("🤖 Raspberry Pi Ultimate Controller")
root.geometry("800x350") # 미디어 컨트롤이 들어가서 높이를 좀 더 키웠습니다
root.configure(bg="#1e1e1e")
root.attributes("-topmost", True)

# 1. 상단: 언어 선택 바
lang_frame = tk.Frame(root, bg="#2d2d2d")
lang_frame.pack(fill='x')

langs = [("한국어", "ko"), ("English", "en"), ("日本語", "ja"), ("Deutsch", "de"), ("원본", "original")]
for text, code in langs:
    btn = tk.Button(lang_frame, text=text, command=lambda c=code: send_language(c),
                   bg="#3e3e3e", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", padx=15, pady=5)
    btn.pack(side='left', padx=5, pady=5)

# 2. 중앙: 자막 표시 영역
lcd_label = tk.Label(root, text="PC에서 URL을 입력하고 영상을 재생하세요",
                     font=("맑은 고딕", 22, "bold"), fg="white", bg="#1e1e1e", wraplength=750, height=3)
lcd_label.pack(expand=True, fill='both', pady=5)

# 3. 💡 중간: 미디어 컨트롤 바 (재생, 정지, 볼륨)
media_frame = tk.Frame(root, bg="#1e1e1e")
media_frame.pack(fill='x', padx=25, pady=5)

# 재생 버튼 (닿자마자 작동하도록 bind 사용)
play_btn = tk.Button(media_frame, text="▶ 재생", bg="#4CAF50", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", width=10)
play_btn.bind("<ButtonPress-1>", lambda event: send_play()) # 💡 터치 즉시 발사!
play_btn.pack(side='left', padx=(0, 10))

# 정지 버튼 (닿자마자 작동하도록 bind 사용)
pause_btn = tk.Button(media_frame, text="⏸ 정지", bg="#f44336", fg="white", font=("맑은 고딕", 10, "bold"), relief="flat", width=10)
pause_btn.bind("<ButtonPress-1>", lambda event: send_pause()) # 💡 터치 즉시 발사!
pause_btn.pack(side='left', padx=(0, 20))

tk.Label(media_frame, text="🔊 음량:", font=("맑은 고딕", 11, "bold"), fg="white", bg="#1e1e1e").pack(side='left')
vol_scale = ttk.Scale(media_frame, from_=0, to=100, orient='horizontal', length=200, command=send_volume)
vol_scale.set(50) 
vol_scale.pack(side='left', padx=10)

# 4. 하단: 시간 및 진행바
player_frame = tk.Frame(root, bg="#1e1e1e")
player_frame.pack(side='bottom', fill='x', pady=(5, 20), padx=25)

time_label = tk.Label(player_frame, text="0:00 / 0:00", font=("Consolas", 12, "bold"), fg="#aaaaaa", bg="#1e1e1e")
time_label.pack(side='left', padx=(0, 20))

canvas = tk.Canvas(player_frame, height=20, bg="#1e1e1e", highlightthickness=0, cursor="hand2")
canvas.pack(side='left', expand=True, fill='x')

canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

root.after(100, lambda: update_ui({"text": "대기 중...", "curr": 0, "total": 1}))
threading.Thread(target=receive_loop, daemon=True).start()

root.mainloop()
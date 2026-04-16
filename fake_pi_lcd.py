import socket
import tkinter as tk
import threading
import json
import time

# 📡 통신 설정
PC_IP = "127.0.0.1"    
MY_PORT = 5005         
PC_PORT = 5006         

sock_receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receive.bind(("0.0.0.0", MY_PORT))
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

last_total_time = 0.1  
is_dragging = False 
last_display_text = ""
last_source_text = ""
subtitle_pages = [""]
MIN_PAGE_SECONDS = 0.9
MAX_PAGE_SECONDS = 3.2

LANG_LABELS = {
    "original": "원본",
    "ko": "한국어 번역",
    "en": "영어 번역",
    "ja": "일본어 번역",
    "zh": "중국어 번역",
    "de": "독일어 번역",
}


def _chunk_text(value, width):
    return [value[i:i + width] for i in range(0, len(value), width)]


def _build_subtitle_pages(text, max_chars_per_line=28):
    if not isinstance(text, str):
        return [""]

    normalized = " ".join(text.strip().split())
    if not normalized:
        return [""]

    words = normalized.split(" ")
    lines = []
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
            for piece in _chunk_text(word, max_chars_per_line):
                lines.append(piece)

    if current:
        lines.append(current)

    pages = []
    for i in range(0, len(lines), 2):
        first = lines[i]
        second = lines[i + 1] if (i + 1) < len(lines) else ""
        pages.append((first + "\n" + second).rstrip())

    return pages if pages else [""]


# === 💡 전송 명령 함수들 ===
def send_language(lang_code):
    sock_send.sendto(f"SET_LANG:{lang_code}".encode('utf-8'), (PC_IP, PC_PORT))

def send_play():
    sock_send.sendto("CMD:PLAY".encode('utf-8'), (PC_IP, PC_PORT))

def send_pause():
    sock_send.sendto("CMD:PAUSE".encode('utf-8'), (PC_IP, PC_PORT))

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
    global last_total_time, is_dragging, last_display_text, last_source_text
    global subtitle_pages
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
    last_total_time = total 

    if title and isinstance(title, str):
        title_label.config(text=f"현재 영상: {title}")

    language_label.config(text=f"현재 자막 언어: {LANG_LABELS.get(lang_code, lang_code)}")

    if isinstance(overlay_text, str) and overlay_text.strip():
        display_text = overlay_text.strip()
        subtitle_pages = [""]
        source_key = "__overlay__"
    else:
        if text != last_source_text:
            subtitle_pages = _build_subtitle_pages(text)

        if subtitle_pages:
            page_count = max(1, len(subtitle_pages))
            page_index = 0

            if isinstance(cue_start, (int, float)) and isinstance(cue_end, (int, float)) and cue_end > cue_start:
                cue_duration = max(0.0, float(cue_end) - float(cue_start))
                elapsed_in_cue = max(0.0, min(float(curr) - float(cue_start), cue_duration))
                per_page = cue_duration / page_count if page_count > 0 else cue_duration
                if per_page <= 0:
                    per_page = 0.001
                # One-way progression: never loop back to first page during same cue.
                page_index = min(int(elapsed_in_cue // per_page), page_count - 1)
            else:
                # Fallback when cue timing is unavailable.
                page_index = 0

            display_text = subtitle_pages[page_index]
        else:
            display_text = ""
        source_key = text

    if source_key != last_source_text or display_text != last_display_text:
        lcd_label.config(text=display_text)
        last_source_text = source_key
        last_display_text = display_text

    if not is_dragging:
        curr_m, curr_s = divmod(int(curr), 60)
        tot_m, tot_s = divmod(int(total), 60)
        time_label.config(text=f"{curr_m}:{curr_s:02d} / {tot_m}:{tot_s:02d}")

        canvas_w = canvas.winfo_width()
        if canvas_w < 10: canvas_w = 600
        canvas.delete("all")
        canvas.create_rectangle(0, 8, canvas_w, 12, fill="#555555", outline="")
        fill_w = max(0, min(canvas_w, (curr / total) * canvas_w))
        canvas.create_rectangle(0, 8, fill_w, 12, fill="#FF0000", outline="")
        canvas.create_oval(fill_w - 6, 4, fill_w + 6, 16, fill="#FF0000", outline="")

def receive_loop():
    while True:
        try:
            data, _ = sock_receive.recvfrom(2048)
            payload = json.loads(data.decode('utf-8'))
            root.after(0, lambda p=payload: update_ui(p))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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
title_label = tk.Label(root, text="현재 영상: 감지 대기 중...",
                       font=("맑은 고딕", 11, "bold"), fg="#d6d6d6", bg="#1e1e1e")
title_label.pack(fill='x', padx=25, pady=(8, 0))

language_label = tk.Label(root, text="현재 자막 언어: 원본",
                          font=("맑은 고딕", 10, "bold"), fg="#9cc9ff", bg="#1e1e1e")
language_label.pack(fill='x', padx=25, pady=(4, 0))

lcd_label = tk.Label(root, text="PC에서 URL을 입력하고 영상을 재생하세요",
                     font=("맑은 고딕", 22, "bold"), fg="white", bg="#1e1e1e", wraplength=750, height=3)
lcd_label.pack(expand=True, fill='both', pady=5)

# 3. 💡 중간: 미디어 컨트롤 바 (재생, 정지)
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